"""Reviewer actions on facts. Used by the review screen (and testable without it)."""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Document, Fact

# Inputs that move a valuation or grade the most: review these first.
CRITICAL_KEYS = {
    "npv_after_tax", "initial_capex", "annual_production", "study_price", "npv_discount_rate", "mine_life_years",
    "ownership_pct", "construction_years", "shares_fully_diluted", "shares_basic", "cash", "debt", "quarterly_cash_burn",
    "resource_contained", "reserve_contained", "recovery", "revenue_ttm", "ebitda_ttm", "op_cash_flow_ttm", "capex_ttm",
    "production_ttm", "aisc_per_unit", "realized_price", "tax_rate", "study_stage", "irr_after_tax",
}


def priority(f: Fact) -> int:
    """0 = a check disagreed or an audit is pending, 1 = a range check failed, 2 = critical and unconfirmed, 3 = the rest."""
    failed = [c for c in json.loads(f.checks or "[]") if not c["passed"]]
    if f.audited == "pending" or any(c["name"] in ("second_reading", "cross_document", "arithmetic") for c in failed):
        return 0
    if failed:
        return 1
    if f.key in CRITICAL_KEYS and f.status == "unverified":
        return 2
    return 3


def queue(session: Session, company_id: int | None, status: str = "unverified", critical_only: bool = False,
          key_contains: str = "", limit: int = 200, needs_attention: bool = False) -> list[Fact]:
    q = select(Fact)
    if company_id is not None:
        q = q.where(Fact.company_id == company_id)
    if status != "all":
        q = q.where(Fact.status == status)
    rows = list(session.scalars(q.order_by(Fact.key, Fact.id)))
    if critical_only:
        rows = [r for r in rows if r.key in CRITICAL_KEYS]
    if key_contains:
        rows = [r for r in rows if key_contains.lower() in r.key.lower()]
    if needs_attention:
        rows = [r for r in rows if priority(r) <= 1]
    rows.sort(key=lambda r: (priority(r), r.auto_check == "quote_found", r.key, r.id))
    return rows[:limit]


def audit_queue(session: Session, company_id: int | None = None) -> list[Fact]:
    q = select(Fact).where(Fact.audited == "pending")
    if company_id is not None:
        q = q.where(Fact.company_id == company_id)
    return list(session.scalars(q.order_by(Fact.id)))


def sample_for_audit(session: Session, rate: float = 0.02, company_id: int | None = None, seed: int | None = None) -> int:
    """Randomly flag some auto-verified facts for a human spot-check, to measure how often the machine is wrong."""
    rng = random.Random(seed)
    q = select(Fact).where(Fact.status == "verified", Fact.verified_by == "auto", Fact.audited.is_(None))
    if company_id is not None:
        q = q.where(Fact.company_id == company_id)
    n = 0
    for f in session.scalars(q):
        if rng.random() < rate:
            f.audited = "pending"
            n += 1
    return n


def record_audit(session: Session, fact_id: int, passed: bool, note: str = "") -> None:
    f = session.get(Fact, fact_id)
    f.audited = "pass" if passed else "fail"
    if not passed:  # a failed audit pulls the fact back out of published cards
        f.status, f.verified_by, f.verified_at = "unverified", None, None
        f.note = " ".join(filter(None, [f.note, "AUDIT FAILED: an auto-verified value was wrong.", note])).strip()


def audit_stats(session: Session) -> dict:
    rows = list(session.scalars(select(Fact).where(Fact.audited.is_not(None))))
    p, fl, pend = (sum(1 for r in rows if r.audited == a) for a in ("pass", "fail", "pending"))
    done = p + fl
    return {"audited": done, "passed": p, "failed": fl, "pending": pend, "error_rate": (fl / done) if done else None}


def set_status(session: Session, fact_id: int, status: str) -> None:
    assert status in ("verified", "rejected", "unverified")
    f = session.get(Fact, fact_id)
    f.status = status
    f.verified_at = datetime.now(timezone.utc).replace(tzinfo=None) if status != "unverified" else None
    f.verified_by = "human" if status == "verified" else None


def override(session: Session, fact_id: int, value: float | None = None, text: str | None = None, note: str = "") -> None:
    """Human correction. The extracted value is kept in the note so nothing is silently lost."""
    f = session.get(Fact, fact_id)
    old = f.value_text if f.value_text is not None else f.value_num
    if value is not None:
        f.value_num = value
    if text is not None:
        f.value_text = text
    f.status, f.verified_at, f.verified_by = "override", datetime.now(timezone.utc).replace(tzinfo=None), "human"
    f.note = " ".join(filter(None, [f.note, f"[reviewer corrected: extracted value was {old}]", note])).strip()


def page_text(session: Session, fact: Fact, context: int = 0) -> str:
    doc = session.get(Document, fact.doc_id) if fact.doc_id else None
    if not doc or not fact.page:
        return ""
    pages = json.loads(doc.pages_json or "[]")
    lo, hi = max(1, fact.page - context), min(len(pages), fact.page + context)
    return "\n\n".join(f"--- page {n} ---\n{pages[n - 1]}" for n in range(lo, hi + 1))


def progress(session: Session, company_id: int) -> dict:
    rows = list(session.scalars(select(Fact).where(Fact.company_id == company_id)))
    out = {"total": len(rows)}
    for s in ("unverified", "verified", "override", "rejected"):
        out[s] = sum(1 for r in rows if r.status == s)
    out["critical_unreviewed"] = sum(1 for r in rows if r.status == "unverified" and r.key in CRITICAL_KEYS)
    return out
