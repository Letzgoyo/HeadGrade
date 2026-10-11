"""Reviewer actions on facts. Used by the review screen (and testable without it)."""
from __future__ import annotations

import json
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


def queue(session: Session, company_id: int, status: str = "unverified", critical_only: bool = False,
          key_contains: str = "", limit: int = 200) -> list[Fact]:
    q = select(Fact).where(Fact.company_id == company_id)
    if status != "all":
        q = q.where(Fact.status == status)
    rows = list(session.scalars(q.order_by(Fact.key, Fact.id)))
    if critical_only:
        rows = [r for r in rows if r.key in CRITICAL_KEYS]
    if key_contains:
        rows = [r for r in rows if key_contains.lower() in r.key.lower()]
    # critical first, then flagged, then the rest
    rows.sort(key=lambda r: (r.key not in CRITICAL_KEYS, r.auto_check == "quote_found", r.key, r.id))
    return rows[:limit]


def set_status(session: Session, fact_id: int, status: str) -> None:
    assert status in ("verified", "rejected", "unverified")
    f = session.get(Fact, fact_id)
    f.status = status
    f.verified_at = datetime.now(timezone.utc).replace(tzinfo=None) if status != "unverified" else None


def override(session: Session, fact_id: int, value: float | None = None, text: str | None = None, note: str = "") -> None:
    """Human correction. The extracted value is kept in the note so nothing is silently lost."""
    f = session.get(Fact, fact_id)
    old = f.value_text if f.value_text is not None else f.value_num
    if value is not None:
        f.value_num = value
    if text is not None:
        f.value_text = text
    f.status, f.verified_at = "override", datetime.now(timezone.utc).replace(tzinfo=None)
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
