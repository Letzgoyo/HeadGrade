"""A FICTIONAL company ("Example Gold Corp") used for tests and demos. None of these numbers are real."""
from __future__ import annotations

from datetime import date

from minecards import db
from minecards.db import Company, Document, Fact, JurisdictionScore, PriceDeck, Project

DECK = {"au": 4000.0, "ag": 48.0}


def add_fact(s, co, key, value=None, text=None, status="verified", project=None, metal=None, category=None, unit=None,
             currency=None, page=1, quote=None, doc=None, as_written=None, as_of=None):
    s.add(Fact(company_id=co.id, project_id=project.id if project else None, key=key, metal=metal, category=category,
               value_num=value, value_text=text, unit=unit, currency=currency, status=status, page=page,
               quote=quote or f"(example) {key}", doc_id=doc.id if doc else None, as_written=as_written, as_of=as_of,
               auto_check="quote_found", confidence=0.8))


def build(s, verified: bool = True) -> Company:
    st = "verified" if verified else "unverified"
    co = Company(name="Example Gold Corp", ticker="EGC", exchange="DEMO", stage="developer", primary_metals="au,ag",
                 jurisdiction="Nevada", currency="USD", held_by_owner=True, sponsored=False)
    s.add(co)
    s.flush()
    p = Project(company_id=co.id, name="Alpha Project", primary_metal="au", jurisdiction="Nevada")
    s.add(p)
    doc = Document(company_id=co.id, kind="technical_report", title="Alpha Project PFS (fictional)", effective_date=date(2026, 3, 1), pages_json="[]")
    fin = Document(company_id=co.id, kind="financials", title="Q2 2026 financial statements (fictional)", effective_date=date(2026, 6, 30), pages_json="[]")
    s.add_all([doc, fin])
    s.add(JurisdictionScore(name="Nevada", score=92.0, source="demo"))
    s.add(PriceDeck(name="spot", as_of=date(2026, 10, 9), prices_json='{"au": 4000, "ag": 48}'))
    s.flush()
    A = lambda *a, **k: add_fact(s, co, *a, status=k.pop("status", st), **k)
    A("shares_fully_diluted", 200e6, doc=fin, page=4, quote="Fully diluted shares outstanding 200,000,000", as_written="200 million", as_of=date(2026, 6, 30))
    A("cash", 80e6, currency="USD", doc=fin, page=3, quote="Cash and cash equivalents 80,000", as_written="80 US$ millions", as_of=date(2026, 6, 30))
    A("debt", 0, currency="USD", doc=fin, page=3, quote="Borrowings nil", as_of=date(2026, 6, 30))
    A("tax_rate", 27, unit="%", doc=doc, page=210, quote="a combined income tax rate of 27%")
    for key, val, unit, ccy, q in [
        ("npv_after_tax", 1.8e9, None, "USD", "after-tax NPV (5%) of US$1,800 million"),
        ("initial_capex", 900e6, None, "USD", "initial capital cost of US$900 million"),
        ("mine_life_years", 12.5, "years", None, "a mine life of 12.5 years"),
        ("construction_years", 3, "years", None, "a three-year construction period"),
        ("npv_discount_rate", 5, "%", None, "discounted at 5%"),
        ("irr_after_tax", 24.1, "%", None, "an after-tax IRR of 24.1%"),
        ("ownership_pct", 100, "%", None, "100% owned"),
    ]:
        A(key, val, project=p, unit=unit, currency=ccy, doc=doc, page=12, quote=q)
    A("annual_production", 250_000, project=p, metal="au", unit="oz", doc=doc, page=12, quote="average annual payable production of 250,000 ounces of gold")
    A("study_price", 2000, project=p, metal="au", unit="oz", doc=doc, page=12, quote="a gold price of US$2,000 per ounce")
    A("study_stage", text="PFS", project=p, doc=doc, page=1, quote="Pre-Feasibility Study")
    A("permit_stage", text="approved", project=p, doc=doc, page=180, quote="the Record of Decision was issued")
    A("jurisdiction", text="Nevada", project=p, doc=doc, page=1, quote="located in Nevada, USA")
    A("team.prior_mines_built", 3, doc=doc, page=2, quote="management has built three previous mines")
    A("team.insider_ownership_pct", 8, unit="%", doc=doc, page=2, quote="directors and officers hold approximately 8%")
    return co
