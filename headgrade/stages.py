"""Infer a company's stage and primary metals from its facts, so thousands of companies need no manual tagging.
Only applied to companies whose stage was auto-assigned; a person's choice is never changed."""
from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from .facts import FactBook
from .registry import METALS


def infer(fb: FactBook) -> tuple[str | None, str]:
    has = lambda key: bool(fb.all(key))
    if has("royalty_revenue_ttm") or has("attributable_production_ttm") or has("portfolio_asset_count"):
        if not has("production_ttm"):
            return "royalty", "royalty/stream revenue or attributable production reported"
    if has("production_ttm") or has("aisc_per_unit") or has("realized_price"):
        return "producer", "production, AISC or realized prices reported"
    stages = {f.value_text for f in fb.all("study_stage")}
    if stages & {"PEA", "PFS", "FS", "construction", "production"} or has("npv_after_tax"):
        return "developer", "an economic study (PEA/PFS/FS) is reported"
    if has("resource_contained") or has("reserve_contained"):
        return "explorer", "mineral resources reported but no economic study or production"
    return None, "not enough facts to tell"


def primary_metals(fb: FactBook, top: int = 3) -> list[str]:
    c = Counter(f.metal for f in fb.facts if f.metal in METALS and f.key in
                ("production_ttm", "annual_production", "resource_contained", "reserve_contained", "realized_price", "aisc_per_unit", "study_price"))
    return [m for m, _ in c.most_common(top)]


def apply(session: Session, company, fb: FactBook) -> dict:
    out = {"changed": False}
    if not company.stage_auto:
        return out
    stage, why = infer(fb)
    metals = primary_metals(fb)
    if stage and stage != company.stage:
        company.stage, out["changed"] = stage, True
    if metals and ",".join(metals) != company.primary_metals:
        company.primary_metals, out["changed"] = ",".join(metals), True
    out.update(stage=stage, why=why, metals=metals)
    return out
