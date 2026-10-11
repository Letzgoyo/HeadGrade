"""Automatic verification, so a person does not have to check every number.

A stored fact is auto-verified only if ALL of these hold:
  1. its quote was found on the cited page (done at ingestion),
  2. it is plausible (inside a sane range for what it is),
  3. no arithmetic or cross-document check contradicts it,
  4. at least ONE independent check agrees: a second, separate reading of the document produced the same
     number, or a different document states the same number for the same date.
A fact that fails any check is sent to a person with the reason. A fact nothing contradicts but nothing
confirms stays unverified (it does not appear in published cards). Humans are never overridden."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Company, Fact
from .extract import ExtractedFact
from .ingest import normalise_value
from .registry import REGISTRY

MAX_REL_TOL = 0.02       # never accept more than 2% apart, however coarsely a number was rounded
AGREE_DAYS = 10          # same figure in another document within this many days counts as confirmation
CONFLICT_DAYS = 3        # a different figure for (almost) the same date counts as a conflict

_PRICE = {"au": (300, 20000), "ag": (3, 500), "pt": (300, 10000), "pd": (300, 10000), "cu": (0.5, 25), "ni": (2, 60),
          "u3o8": (5, 500), "zn": (0.3, 10), "pb": (0.3, 10)}
_PROD = {"au": (0, 2e7), "pt": (0, 2e7), "pd": (0, 2e7), "ag": (0, 3e8), "cu": (0, 1e10), "ni": (0, 1e10), "zn": (0, 1e10),
         "pb": (0, 1e10), "u3o8": (0, 1e8)}
_CONTAINED = {"au": (1e2, 3e9), "pt": (1e2, 1e9), "pd": (1e2, 1e9), "ag": (1e4, 1e11), "cu": (1e6, 1e12), "ni": (1e6, 1e12),
              "zn": (1e6, 1e12), "pb": (1e6, 1e12), "u3o8": (1e4, 1e10)}
_FLAT = {
    "shares_basic": (1e5, 3e11), "shares_fully_diluted": (1e5, 3e11), "cash": (0, 5e11), "debt": (0, 5e11),
    "quarterly_cash_burn": (0, 1e10), "revenue_ttm": (0, 5e11), "ebitda_ttm": (-1e10, 2e11), "op_cash_flow_ttm": (-2e10, 2e11),
    "capex_ttm": (0, 1e11), "reserve_life_years": (0.3, 100), "tax_rate": (0, 70), "royalty_revenue_ttm": (0, 1e11),
    "portfolio_asset_count": (1, 5000), "top_asset_share_pct": (0, 100), "npv_after_tax": (-5e10, 5e11), "npv_discount_rate": (0, 25),
    "irr_after_tax": (-50, 300), "initial_capex": (1e5, 1e11), "mine_life_years": (0.3, 100), "construction_years": (0, 15),
    "royalty_burden_pct": (0, 50), "ownership_pct": (0, 100), "project_weight_pct": (0, 100), "recovery": (10, 100),
    "team.insider_ownership_pct": (0, 100), "team.prior_mines_built": (0, 50), "team.regulatory_actions": (0, 200),
    "resource_grade": (0, 1e5),
}


def bounds_for(key: str, metal: str | None):
    if key in ("aisc_per_unit", "realized_price", "study_price"):
        return _PRICE.get(metal or "")
    if key in ("production_ttm", "annual_production", "attributable_production_ttm"):
        return _PROD.get(metal or "")
    if key in ("resource_contained", "reserve_contained"):
        return _CONTAINED.get(metal or "")
    return _FLAT.get(key)


def plausible(key: str, metal: str | None, value: float | None) -> tuple[bool | None, str]:
    b = bounds_for(key, metal)
    if b is None or value is None:
        return None, ""
    ok = b[0] <= value <= b[1]
    return ok, "" if ok else f"{value:g} is outside the plausible range {b[0]:g} to {b[1]:g} for {key}{' ' + metal if metal else ''}"


def _half_step(x: float) -> float:
    """Half of the last significant digit's place value: how far a rounded figure could be from the truth."""
    d = Decimal(f"{abs(x):.12g}").normalize()
    return 0.5 * float(Decimal(1).scaleb(d.as_tuple().exponent)) if x else 0.0


def close(a: float | None, b: float | None) -> bool:
    """Equal once rounding is accounted for. '3.06 billion' agrees with 3,061,238 thousand; 2,356 and 2,365 do not,
    because both are written to the unit (a transposed digit is exactly the error we want to catch)."""
    if a is None or b is None:
        return False
    mx = max(abs(a), abs(b))
    tol = min(max(_half_step(a), _half_step(b)), MAX_REL_TOL * mx)
    return abs(a - b) <= tol + 1e-9 * max(mx, 1e-12)


@dataclass
class Second:
    key: str
    metal: str | None
    category: str | None
    project: str
    num: float | None
    text: str | None


def from_extracted(ef: ExtractedFact) -> Second:
    n = normalise_value(ef)
    return Second(ef.key, ef.metal, ef.category, (ef.project or "").strip().lower(), n.value_num, n.value_text)


def _proj_match(a: str, b: str) -> bool:
    return not a or not b or a == b or a in b or b in a


def _same_value(row: Fact, num: float | None, text: str | None) -> bool:
    if row.value_num is not None and num is not None:
        return close(row.value_num, num)
    if row.value_text is not None and text is not None:
        return row.value_text.strip().casefold() == text.strip().casefold()
    return False


def compare_second(row: Fact, project_name: str, second: list[Second]) -> str:
    scoped = REGISTRY[row.key].scope == "project"
    cands = [x for x in second if x.key == row.key and (x.metal or None) == (row.metal or None)
             and (x.category or None) == (row.category or None) and (not scoped or _proj_match(project_name, x.project))]
    if not cands:
        return "none"
    return "agree" if any(_same_value(row, x.num, x.text) for x in cands) else "conflict"


def compare_cross_doc(row: Fact, others: list[Fact]) -> str:
    if row.as_of is None:
        return "none"
    cands = [o for o in others if o.key == row.key and o.metal == row.metal and o.category == row.category
             and o.project_id == row.project_id and o.as_of is not None and o.doc_id != row.doc_id]
    if any(abs((o.as_of - row.as_of).days) <= AGREE_DAYS and _same_value(row, o.value_num, o.value_text) for o in cands):
        return "agree"
    if any(abs((o.as_of - row.as_of).days) <= CONFLICT_DAYS and not _same_value(row, o.value_num, o.value_text) for o in cands):
        return "conflict"
    return "none"


def arithmetic_conflicts(rows: list[Fact]) -> dict[int, list[str]]:
    """Internal-consistency checks within a document. Maps fact id -> reasons it is implicated."""
    out: dict[int, list[str]] = defaultdict(list)
    by = defaultdict(dict)
    for r in rows:
        if r.key == "resource_contained" and r.value_num is not None:
            by[(r.project_id, r.metal, r.doc_id)][r.category] = r
    for (_, metal, _), cats in by.items():
        if all(c in cats for c in ("measured", "indicated", "measured_indicated")):
            m, i, mi = (cats[c].value_num for c in ("measured", "indicated", "measured_indicated"))
            if abs((m + i) - mi) > 0.01 * max(mi, 1):
                for c in cats.values():
                    out[c.id].append(f"measured ({m:g}) + indicated ({i:g}) does not equal measured_indicated ({mi:g}) for {metal}")
    proj = defaultdict(dict)
    for r in rows:
        if r.key in ("npv_after_tax", "npv_discount_rate", "irr_after_tax") and r.value_num is not None:
            proj[(r.project_id, r.doc_id)][r.key] = r
    for d in proj.values():
        if all(k in d for k in ("npv_after_tax", "npv_discount_rate", "irr_after_tax")):
            npv, rate, irr = (d[k].value_num for k in ("npv_after_tax", "npv_discount_rate", "irr_after_tax"))
            if (npv > 0 and irr < rate - 0.5) or (npv < 0 and irr > rate + 0.5):
                for r in d.values():
                    out[r.id].append(f"NPV ({npv:,.0f}) is inconsistent with an IRR of {irr:g}% against a {rate:g}% discount rate")
    return out


def decide(row: Fact, second: str, cross: str, arith: list[str]) -> tuple[str, list[dict]]:
    checks: list[dict] = []
    add = lambda name, passed, detail="": checks.append({"name": name, "passed": passed, "detail": detail})
    add("quote_on_page", row.auto_check == "quote_found", row.auto_check or "")
    ok, why = plausible(row.key, row.metal, row.value_num)
    if ok is not None:
        add("plausible_range", ok, why)
    if second != "none":
        add("second_reading", second == "agree", "an independent reading " + ("matched" if second == "agree" else "gave a different value"))
    if cross != "none":
        add("cross_document", cross == "agree", "another document " + ("states the same figure" if cross == "agree" else "states a different figure for the same date"))
    if arith:
        add("arithmetic", False, "; ".join(arith))
    if any(not c["passed"] for c in checks):
        return "needs_review", checks
    if second == "agree" or cross == "agree":
        return "auto_verified", checks
    return "unconfirmed", checks


@dataclass
class AutoStats:
    auto_verified: int = 0
    needs_review: int = 0
    unconfirmed: int = 0

    def as_dict(self):
        return self.__dict__.copy()


def _commit(r: Fact, decision: str, checks: list[dict], now: datetime, stats: "AutoStats") -> None:
    r.checks = json.dumps(checks)
    if decision == "auto_verified":
        r.status, r.verified_by, r.verified_at = "verified", "auto", now
        r.confidence = max(r.confidence or 0, 0.9)
        stats.auto_verified += 1
    elif decision == "needs_review":
        why = "; ".join(c["detail"] or c["name"] for c in checks if not c["passed"])
        if "NEEDS REVIEW" not in (r.note or ""):
            r.note = ((r.note + "; ") if r.note else "") + "NEEDS REVIEW: " + why
        stats.needs_review += 1
    else:
        stats.unconfirmed += 1


def reconsider(session: Session, company: Company) -> "AutoStats":
    """When a NEW document arrives it can confirm (or contradict) facts from older ones. Re-check every fact still unverified."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    allrows = list(session.scalars(select(Fact).where(Fact.company_id == company.id, Fact.status != "rejected")))
    pending = [r for r in allrows if r.status == "unverified" and r.auto_check == "quote_found"
               and not any(not c["passed"] for c in json.loads(r.checks or "[]") if c["name"] != "cross_document")]
    arith = arithmetic_conflicts(allrows)
    stats = AutoStats()
    for r in pending:
        c = compare_cross_doc(r, [o for o in allrows if o.id != r.id])
        prior_second = next((c_ for c_ in json.loads(r.checks or "[]") if c_["name"] == "second_reading"), None)
        decision, checks = decide(r, "agree" if prior_second and prior_second["passed"] else "none", c, arith.get(r.id, []))
        if decision == "unconfirmed":
            continue
        _commit(r, decision, checks, now, stats)
    session.flush()
    return stats


def apply(session: Session, company: Company, doc_id: int, second: list[ExtractedFact] | None = None) -> AutoStats:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = list(session.scalars(select(Fact).where(Fact.company_id == company.id, Fact.doc_id == doc_id, Fact.status == "unverified")))
    others = list(session.scalars(select(Fact).where(Fact.company_id == company.id, Fact.doc_id != doc_id, Fact.status != "rejected")))
    names = {p.id: p.name.strip().lower() for p in company.projects}
    sec = [from_extracted(x) for x in (second or [])]
    arith = arithmetic_conflicts(rows)
    stats = AutoStats()
    for r in rows:
        s_res = compare_second(r, names.get(r.project_id, ""), sec) if second is not None else "none"
        c_res = compare_cross_doc(r, others)
        decision, checks = decide(r, s_res, c_res, arith.get(r.id, []))
        _commit(r, decision, checks, now, stats)
    session.flush()
    return stats
