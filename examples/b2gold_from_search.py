"""B2Gold draft card built from WEB-SEARCH SUMMARIES (secondary), extracted by hand, not by the Claude reader.

Why: the sandbox cannot download B2Gold's filings. The facts below are copied from search-result text, stored with the
source URL, run through the real quote check / unit conversion / valuation / grading code, and left UNVERIFIED.
Replace them by ingesting the real documents (see README)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from minecards import db, ingest, pipeline
from minecards.db import Company, PriceDeck
from minecards.extract import ExtractedFact as EF, ExtractionResult
from minecards.render import render_html
from minecards.valuation import Market


def fake_client(facts):
    return NS(messages=NS(parse=lambda **kw: NS(parsed_output=ExtractionResult(facts=facts), stop_reason="end_turn")))


# (title, kind, url, effective date, page text, facts)
SOURCES = [
    ("FY2025 results (web-search summary, secondary)", "financials",
     "https://www.sec.gov/Archives/edgar/data/1429937/000142993726000004/btg-123125xexhibit992.htm", date(2025, 12, 31),
     "Gold revenue ($ in thousands) 1,053,977 499,788 3,061,238 1,902,030 1,934,272\n"
     "record annual revenue of $3.06 billion after selling 927,797 ounces of gold at an average realized price of $3,299 per ounce\n"
     "Consolidated gold production for 2025 was 979,604 ounces, including 14,554 ounces of pre-commercial production from the Goose Mine\n"
     "full-year all-in sustaining costs (AISC) totaled $1,584 per ounce\n"
     "Operating cash flows for 2025 totaled $940 million before working capital adjustments; $896 million after adjustments, with $286 million generated in Q4.",
     [EF(key="revenue_ttm", value=3061238, unit="US$ thousands", page=1, quote="Gold revenue ($ in thousands) 1,053,977 499,788 3,061,238 1,902,030 1,934,272", as_of="2025-12-31"),
      EF(key="production_ttm", metal="au", value=927797, unit="ounces", page=1, quote="selling 927,797 ounces of gold at an average realized price of $3,299 per ounce", as_of="2025-12-31"),
      EF(key="realized_price", metal="au", value=3299, unit="US$/oz", page=1, quote="average realized price of $3,299 per ounce", as_of="2025-12-31"),
      EF(key="aisc_per_unit", metal="au", value=1584, unit="US$/oz", page=1, quote="full-year all-in sustaining costs (AISC) totaled $1,584 per ounce", as_of="2025-12-31"),
      EF(key="op_cash_flow_ttm", value=896, unit="US$ millions", page=1, quote="$940 million before working capital adjustments; $896 million after adjustments", as_of="2025-12-31")]),
    ("Q2 2026 results (web-search summary, secondary: Quartr)", "financials",
     "https://quartr.com/events/b2gold-corp-bto-q2-2026_ofrpN4Ow", date(2026, 6, 30),
     "Cash and equivalents were $287 million at June 30, 2026, and working capital was $405 million.\n"
     "All-in sustaining costs were $2,356 per ounce sold.\n"
     "Free cash outflow was $258 million.",
     [EF(key="cash", value=287, unit="US$ millions", page=1, quote="Cash and equivalents were $287 million at June 30, 2026", as_of="2026-06-30"),
      EF(key="aisc_per_unit", metal="au", value=2356, unit="US$/oz", page=1, quote="All-in sustaining costs were $2,356 per ounce sold.", as_of="2026-06-30")]),
    ("Form 6-K, May 2026: share count (web-search summary, secondary)", "other",
     "https://www.sec.gov/Archives/edgar/data/0001429937/000142993726000007/exhibit992-33126.htm", date(2026, 5, 6),
     "A Form 6-K filing reports 1,332,417,631 common shares were outstanding as of May 6, 2026. It also notes the Notes, if converted, would result in the issuance of approximately 145 million shares.",
     [EF(key="shares_basic", value=1332417631, page=1, quote="1,332,417,631 common shares were outstanding as of May 6, 2026", as_of="2026-05-06")]),
    ("Convertible notes offering (web-search summary, secondary)", "press_release",
     "https://www.tipranks.com/news/company-announcements/b2gold-completes-460m-convertible-notes-offering-to-bolster-financial-strategy", date(2025, 1, 28),
     "B2Gold issued convertible senior unsecured notes with an aggregate principal amount of $460 million on January 28, 2025, maturing February 1, 2030.",
     [EF(key="debt", value=460, unit="US$ millions", page=1, quote="aggregate principal amount of $460 million on January 28, 2025", as_of="2026-06-30")]),
]


def main(out_html: str) -> None:
    engine = db.make_engine()
    db.init_db(engine)
    with db.session_scope(engine) as s:
        co = s.scalar(select(Company).where(Company.ticker == "BTO.TO"))
        if not co:
            co = Company(name="B2Gold Corp", ticker="BTO.TO", exchange="TSX", stage="producer", primary_metals="au",
                         jurisdiction="Mali, Namibia, Philippines, Canada", currency="CAD", held_by_owner=True)
            s.add(co)
            s.flush()
        for title, kind, url, eff, page, facts in SOURCES:
            r = ingest.ingest_pages(s, co, [page], kind, title, fake_client(facts), eff, url)
            print(f"{title[:60]:60} -> extracted {r.extracted}, saved {r.saved}, auto-rejected {r.auto_rejected}, duplicates {r.skipped_duplicates}")
        debt = s.scalar(select(db.Fact).where(db.Fact.company_id == co.id, db.Fact.key == "debt"))
        if debt and "revolver" not in (debt.note or ""):
            debt.note = ((debt.note or "") + " Principal of the convertible notes only: the revolving credit facility balance at June 30, 2026 was not found. "
                         "The notes convert into about 145 million shares; at current prices that is worth more than the $460M principal.").strip()
        if not s.scalar(select(PriceDeck).where(PriceDeck.as_of == date(2026, 9, 21))):
            s.add(PriceDeck(name="spot", as_of=date(2026, 9, 21), prices_json='{"au": 4367.35}',
                            note="latest dated gold price found by web search (bullion-dealer page); update it"))
        s.flush()
        deck = pipeline.latest_deck(s)
        # price: C$7.29 from the owner's broker screenshot; USD per CAD 0.70193 on 2026-10-02 (exchange-rates.org)
        mk = Market(7.29, "CAD", date(2026, 10, 9), {"USD": 1.0, "CAD": 0.70193})
        card = pipeline.build_card(s, co, deck, mk, publish=False, today=date(2026, 10, 11))
        Path(out_html).write_text(render_html(card, ["Draft built from web-search summaries, not from the filings. Nothing here is verified."]))
        g, v = card["grade"], card["valuation"]
        m = v["metrics"]
        print(f"\nquality {g['quality_grade']} ({g['quality_score']})  valuation {g['value_grade']} ({g['value_score']})  confidence {g['confidence']['level']}")
        print("quadrant:", g["quadrant"])
        for k in ("market_cap_usd", "ev_usd", "net_debt_usd", "price_to_operating_cash_flow", "ev_to_revenue", "aisc_margin_at_deck", "fcf_ttm_usd", "ev_to_ebitda"):
            print(f"  {k}: {m.get(k)}")
        print("flags:", *v["flags"], sep="\n  - ")
        print("missing:", g["missing"])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "b2gold_card.html")
