"""End-to-end demo on a FICTIONAL company with FAKE SEC documents (no network, no API key).
Shows the weekly run, the review queue (one deliberate conflict), and the card before and after a person resolves it.
Usage: python examples/demo_pipeline.py <output_dir>"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from headgrade import config, db, edgar, fraser, pipeline, prices, review, weekly
from headgrade.edgar import Edgar, html_to_pages
from headgrade.extract import ExtractedFact as EF, ExtractionResult
from headgrade.render import render_html

FILL = "<p>" + "Operations continued as planned during the quarter and the production profile is unchanged. " * 30 + "</p>"
RELEASE = ("<html><body><p>Example Mining Corp. (FICTIONAL) reports second quarter 2026 results</p>"
           "<p>Gold sold for the twelve months ended June 30, 2026 was 640,000 ounces at an average realized price of $3,360 per ounce.</p>"
           "<p>All-in sustaining costs for the second quarter were $2,010 per ounce sold.</p>"
           "<p>Revenue for the twelve months ended June 30, 2026 was $2,150 million.</p>"
           "<p>Adjusted EBITDA for the twelve months ended June 30, 2026 was $1,020 million.</p>"
           "<p>Cash and cash equivalents were $310 million at June 30, 2026.</p>" + FILL * 3 + "</body></html>")
STATEMENTS = ("<html><body><p>Example Mining Corp. Management's Discussion and Analysis for the quarter ended June 30, 2026</p>"
              "<p>Cash flow from operations for the twelve months ended June 30, 2026 was $780 million.</p>"
              "<p>Capital expenditures for the twelve months ended June 30, 2026 totalled $410 million.</p>"
              "<p>Total debt was $420 million, comprising convertible notes and amounts drawn on the revolving credit facility.</p>"
              "<p>Cash and cash equivalents were $310 million at June 30, 2026.</p>"
              "<p>Revenue for the twelve months ended June 30, 2026 was $2,150 million.</p>"
              "<p>As of August 1, 2026, 800,000,000 common shares were outstanding.</p>"
              "<p>The Company's combined statutory income tax rate is 28%.</p>" + FILL * 3 + "</body></html>")
AIF = ("<html><body><p>Example Mining Corp. Annual Information Form for the year ended December 31, 2025</p>"
       "<p>The Kala Mine is located in Mali.</p><p>The Northland Mine is located in Ontario.</p>"
       "<p>The Kala Mine accounted for 45% of 2025 gold production and the Northland Mine accounted for 55%.</p>"
       "<p>Both mines are fully permitted and operating.</p>"
       "<p>Directors and officers as a group beneficially own approximately 4.5% of the outstanding common shares.</p>"
       "<p>The Company's management team has previously built three mines.</p>"
       "<p>Based on mineral reserves, the mine life at current production is nine years.</p>" + FILL * 3 + "</body></html>")
DOCS = {"exhibit991.htm": RELEASE, "exhibit992.htm": STATEMENTS, "exhibit993.htm": AIF}
SUB = {"sic": "1040", "name": "EXAMPLE MINING CORP (FICTIONAL)", "exchanges": ["NYSE American"], "filings": {"recent": {
    "accessionNumber": ["0009999999-26-000010"], "form": ["6-K"], "filingDate": ["2026-08-06"], "reportDate": ["2026-06-30"],
    "primaryDocument": ["form6-k.htm"], "primaryDocDescription": ["6-K"]}}}


def fake_edgar() -> Edgar:
    def getter(url):
        if "submissions" in url:
            return json.dumps(SUB).encode()
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"cik_str": 9999999, "ticker": "EXM", "title": "EXAMPLE MINING CORP"}}).encode()
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": n} for n in DOCS] + [{"name": "form6-k.htm"}]}}).encode()
        name = url.rsplit("/", 1)[1]
        return b"<html><body>cover</body></html>" if name == "form6-k.htm" else DOCS[name].encode()
    return Edgar(getter=getter, min_interval=0)


def ef(doc: str, key: str, quote: str, **kw) -> EF:
    pages = html_to_pages(DOCS[doc])
    pg = next(i for i, p in enumerate(pages, 1) if " ".join(quote.split()) in " ".join(p.split()))
    return EF(key=key, quote=quote, page=pg, **kw)


def readings(reader_b: bool):
    r, st, a = "exhibit991.htm", "exhibit992.htm", "exhibit993.htm"
    capex = 401.0 if reader_b else 410.0            # reader B transposes two digits
    out = {
        r: [ef(r, "production_ttm", "was 640,000 ounces at an average realized price of $3,360 per ounce", metal="au", value=640000, unit="ounces", as_of="2026-06-30"),
            ef(r, "realized_price", "average realized price of $3,360 per ounce", metal="au", value=3360, unit="US$/oz", as_of="2026-06-30"),
            ef(r, "aisc_per_unit", "All-in sustaining costs for the second quarter were $2,010 per ounce sold", metal="au", value=2010, unit="US$/oz", as_of="2026-06-30"),
            ef(r, "revenue_ttm", "Revenue for the twelve months ended June 30, 2026 was $2,150 million", value=2150, unit="US$ millions", as_of="2026-06-30"),
            ef(r, "ebitda_ttm", "Adjusted EBITDA for the twelve months ended June 30, 2026 was $1,020 million", value=1020, unit="US$ millions", as_of="2026-06-30"),
            ef(r, "cash", "Cash and cash equivalents were $310 million at June 30, 2026", value=310, unit="US$ millions", as_of="2026-06-30")],
        st: [ef(st, "op_cash_flow_ttm", "Cash flow from operations for the twelve months ended June 30, 2026 was $780 million", value=780, unit="US$ millions", as_of="2026-06-30"),
             ef(st, "capex_ttm", f"Capital expenditures for the twelve months ended June 30, 2026 totalled $410 million", value=capex, unit="US$ millions", as_of="2026-06-30"),
             ef(st, "debt", "Total debt was $420 million", value=420, unit="US$ millions", as_of="2026-06-30"),
             ef(st, "cash", "Cash and cash equivalents were $310 million at June 30, 2026", value=310, unit="US$ millions", as_of="2026-06-30"),
             ef(st, "revenue_ttm", "Revenue for the twelve months ended June 30, 2026 was $2,150 million", value=2150, unit="US$ millions", as_of="2026-06-30"),
             ef(st, "shares_basic", "800,000,000 common shares were outstanding", value=800000000, as_of="2026-08-01"),
             ef(st, "tax_rate", "combined statutory income tax rate is 28%", value=28, unit="%")],
        a: [ef(a, "jurisdiction", "The Kala Mine is located in Mali", project="Kala Mine", text="Mali"),
            ef(a, "jurisdiction", "The Northland Mine is located in Ontario", project="Northland Mine", text="Ontario"),
            ef(a, "project_weight_pct", "The Kala Mine accounted for 45% of 2025 gold production", project="Kala Mine", value=45, unit="%"),
            ef(a, "project_weight_pct", "the Northland Mine accounted for 55%", project="Northland Mine", value=55, unit="%"),
            ef(a, "permit_stage", "Both mines are fully permitted and operating", project="Kala Mine", text="fully permitted"),
            ef(a, "permit_stage", "Both mines are fully permitted and operating", project="Northland Mine", text="fully permitted"),
            ef(a, "team.insider_ownership_pct", "own approximately 4.5% of the outstanding common shares", value=4.5, unit="%"),
            ef(a, "team.prior_mines_built", "management team has previously built three mines", value=3),
            ef(a, "reserve_life_years", "the mine life at current production is nine years", value=9, unit="years")]}
    if reader_b:   # the second reader misses two statements entirely (so they stay unconfirmed)
        out[a] = [x for x in out[a] if x.key not in ("team.prior_mines_built", "reserve_life_years")]
    return out


class Reader:
    """Fake Claude: returns whichever document's facts match the pages it is shown."""
    def __init__(self, second: bool):
        self.by_doc = readings(second)
        self.messages = NS(parse=self._parse)

    def _parse(self, **kw):
        content = kw["messages"][0]["content"]
        facts = []
        for name, fs in self.by_doc.items():
            if any(f.quote in " ".join(content.split()) for f in fs[:1]):
                facts = fs
        return NS(parsed_output=ExtractionResult(facts=facts), stop_reason="end_turn")


def build(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    path = out / "demo.db"
    path.unlink(missing_ok=True)
    config.DATA_DIR = out
    engine = db.make_engine(f"sqlite:///{path}")
    db.init_db(engine)
    pdf = Path("/root/.claude/uploads/db4f91d2-af24-59cf-9de0-9fcf948172ab/cf5927c8-annual-survey-of-mining-companies-2025.pdf")
    with db.session_scope(engine) as s:
        if pdf.exists():
            fraser.load(s, fraser.parse_pdf(str(pdf)))
        else:
            s.add_all([db.JurisdictionScore(name="Mali", score=7.36, source="Fraser 2025"), db.JurisdictionScore(name="Ontario", score=93.70, source="Fraser 2025")])
        s.add(db.Company(name="Example Mining Corp (FICTIONAL)", ticker="EXM", stage="explorer", stage_auto=True, currency="USD",
                         held_by_owner=True, primary_metals=""))
        s.flush()
        prices.set_price(s, "EXM", 6.00, "USD", date(2026, 10, 9))
        s.add(db.PriceDeck(name="spot", as_of=date(2026, 10, 9), prices_json='{"au": 4367.35}'))
    summary = weekly.run_weekly(engine, fake_edgar(), Reader(False), Reader(True), today=date(2026, 10, 11))
    shutil.copy(path, out / "demo_before.db")
    # a person opens the review queue, sees the capex conflict, checks the document (410) and approves the correct value
    with db.session_scope(engine) as s:
        fid = review.queue(s, None, needs_attention=True)[0].id
        review.override(s, fid, value=410e6, note="checked against the MD&A: $410 million")
        for f in s.scalars(select(db.Fact).where(db.Fact.status == "unverified", db.Fact.key.in_(("reserve_life_years", "team.prior_mines_built")))):
            review.set_status(s, f.id, "verified")
        co = s.scalar(select(db.Company))
        card, status, changes = weekly.build_company_card(s, co, date(2026, 10, 11), publish=True, save=False)
        (out / "card_after.html").write_text(render_html(card, ["A reviewer resolved the capex conflict and confirmed two facts only one reader had found."]))
    return summary, engine


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_out")
    summary, engine = build(out)
    for r in summary["results"]:
        print(json.dumps({k: r[k] for k in ("ticker", "filings_new", "docs", "facts", "auto_verified", "needs_review", "stage", "card_status", "quality", "valuation")}))
    with db.session_scope(engine) as s:
        print("\nwhat needs a person (before):")
        shutil.copy(out / "demo_before.db", out / "tmp.db")
