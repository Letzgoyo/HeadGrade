import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from headgrade.render import render_html
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headgrade import db, prices, review, stages, universe, weekly
from headgrade.edgar import Edgar
from headgrade.extract import ExtractedFact as EF, ExtractionResult
from headgrade.facts import F, FactBook

FILLER = "<p>Quarterly production and mineral resource update for the quarter. " + "Operations continued as planned. " * 40 + "</p>"
RELEASE = ("<html><body><p>B2Gold Corp. reports Q2 2026 results</p>"
           "<p>Cash and equivalents were $287 million at June 30, 2026, and working capital was $405 million.</p>"
           "<p>All-in sustaining costs were $2,356 per ounce sold.</p>"
           "<p>Gold production was 209,537 ounces sold at an average realized price of $3,767 per ounce.</p>"
           "<p>1,332,417,631 common shares were outstanding as of May 6, 2026.</p>" + FILLER * 3 + "</body></html>")

SUB = {"sic": "1040", "name": "B2GOLD CORP", "exchanges": ["NYSE American"], "filings": {"recent": {
    "accessionNumber": ["0001429937-26-000010"], "form": ["6-K"], "filingDate": ["2026-08-06"], "reportDate": ["2026-06-30"],
    "primaryDocument": ["form6-k.htm"], "primaryDocDescription": ["6-K"]}}}


def facts(aisc=2356.0):
    q = lambda s: s
    return [
        EF(key="cash", value=287.0, unit="US$ millions", page=1, quote="Cash and equivalents were $287 million at June 30, 2026", as_of="2026-06-30"),
        EF(key="aisc_per_unit", metal="au", value=aisc, unit="US$/oz", page=1, quote="All-in sustaining costs were $2,356 per ounce sold", as_of="2026-06-30"),
        EF(key="production_ttm", metal="au", value=209537.0, unit="ounces", page=1, quote="production was 209,537 ounces sold", as_of="2026-06-30"),
        EF(key="shares_basic", value=1332417631.0, page=1, quote="1,332,417,631 common shares were outstanding as of May 6, 2026", as_of="2026-05-06"),
    ]


class Reader:
    def __init__(self, fs):
        self.fs, self.calls = fs, 0
        self.messages = NS(parse=self._parse)

    def _parse(self, **kw):
        self.calls += 1
        return NS(parsed_output=ExtractionResult(facts=self.fs), stop_reason="end_turn")


def fake_edgar(sub, docs=None, fail=()):
    docs = docs or {"exhibit991.htm": RELEASE}

    def getter(url):
        if "submissions" in url:
            return json.dumps(sub).encode()
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"cik_str": 1429937, "ticker": "BTG", "title": "B2GOLD CORP"}}).encode()
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": n} for n in list(docs) + ["form6-k.htm"]]}}).encode()
        name = url.rsplit("/", 1)[1]
        if name in fail:
            raise RuntimeError("download failed")
        if name == "form6-k.htm":
            return b"<html><body>cover</body></html>"
        return docs[name].encode()
    return Edgar(getter=getter, min_interval=0)


@pytest.fixture
def engine():
    e = db.make_engine("sqlite:///:memory:")
    db.init_db(e)
    return e


def add_company(s, **kw):
    co = db.Company(name="B2Gold Corp", ticker="BTG", stage="explorer", stage_auto=True, currency="USD", primary_metals="", **kw)
    s.add(co)
    s.flush()
    return co


def test_weekly_end_to_end_two_readings_one_conflict(engine):
    with db.session_scope(engine) as s:
        co = add_company(s)
        prices.set_price(s, "BTG", 5.12, "USD", date(2026, 10, 9))
        s.add(db.PriceDeck(name="spot", as_of=date(2026, 10, 9), prices_json='{"au": 4367.35}'))
        a, b = Reader(facts()), Reader(facts(aisc=2365.0))                     # reader B disagrees about AISC
        run = weekly.process_company(s, co, fake_edgar(SUB), a, b, today=date(2026, 10, 11))
        assert (run.filings_new, run.docs, run.facts) == (1, 1, 4) and run.errors == []
        assert (run.auto_verified, run.needs_review) == (3, 1)
        by = {f.key: f for f in s.scalars(select(db.Fact))}
        assert by["cash"].status == by["shares_basic"].status == by["production_ttm"].status == "verified" and by["cash"].verified_by == "auto"
        assert by["aisc_per_unit"].status == "unverified" and "NEEDS REVIEW" in by["aisc_per_unit"].note
        assert by["cash"].value_num == 287e6 and by["cash"].currency == "USD"
        # filing recorded, stage and metals inferred from the facts, card built from verified facts only
        fil = s.scalars(select(db.Filing)).one()
        assert (fil.status, fil.n_docs, fil.n_facts) == ("processed", 1, 4) and co.last_filing_seen == date(2026, 8, 6) and co.cik == "0001429937"
        assert (co.stage, co.primary_metals) == ("producer", "au")
        assert run.card_status == "ok" and run.changes == ["First card for this company."]
        snap = s.scalars(select(db.Snapshot)).one()
        card = json.loads(snap.card_json)
        assert card["mode"] == "published" and card["grade"]["confidence"]["auto_verified"] >= 1
        assert card["checklist"]["applicable"] and len(card["checklist"]["squares"]) == 25
        assert "Project scorecard" in render_html(card)
        assert not any(e["label"].startswith("AU AISC") for e in card["evidence"])      # the disputed number is not used
        # the disputed fact is first in a person's queue
        assert review.queue(s, co.id)[0].key == "aisc_per_unit"
        assert review.queue(s, co.id, needs_attention=True)[0].key == "aisc_per_unit"


def test_second_run_is_idempotent_and_picks_up_only_new_filings(engine):
    with db.session_scope(engine) as s:
        co = add_company(s)
        a, b = Reader(facts()), Reader(facts())
        sub = json.loads(json.dumps(SUB))
        edgar = fake_edgar(sub)
        weekly.process_company(s, co, edgar, a, b, build=False)
        calls = (a.calls, b.calls)
        run2 = weekly.process_company(s, co, edgar, a, b, build=False)
        assert run2.filings_new == 0 and (a.calls, b.calls) == calls              # nothing re-read, nothing re-billed
        # a new filing with the same figures a month later: cross-document agreement + new document recorded
        rec = sub["filings"]["recent"]
        for k, v in (("accessionNumber", "0001429937-26-000050"), ("form", "6-K"), ("filingDate", "2026-09-10"), ("reportDate", "2026-06-30"),
                     ("primaryDocument", "form6-k.htm"), ("primaryDocDescription", "6-K")):
            rec[k].append(v)
        edgar2 = fake_edgar(sub, docs={"exhibit991.htm": RELEASE.replace("Q2 2026", "Q2 2026 (revised)")})
        run3 = weekly.process_company(s, co, edgar2, a, b, build=False)
        assert run3.filings_new == 1 and run3.docs == 1
        assert len(list(s.scalars(select(db.Filing)))) == 2 and co.last_filing_seen == date(2026, 9, 10)


def test_a_failing_download_is_isolated_and_recorded(engine):
    with db.session_scope(engine) as s:
        co = add_company(s)
        edgar = fake_edgar(SUB, docs={"exhibit991.htm": RELEASE}, fail=("exhibit991.htm",))
        run = weekly.process_company(s, co, edgar, Reader(facts()), Reader(facts()), build=False)
        assert run.filings_error == 1 and "download failed" in run.errors[0]
        fil = s.scalars(select(db.Filing)).one()
        assert fil.status == "error" and "download failed" in fil.error
        assert list(s.scalars(select(db.Fact))) == []                              # nothing half-written


def test_unknown_ticker_and_missing_price_are_reported_not_fatal(engine):
    with db.session_scope(engine) as s:
        co = db.Company(name="Mystery", ticker="ZZZZ", stage="explorer", currency="USD", primary_metals="")
        s.add(co)
        s.flush()
        run = weekly.process_company(s, co, fake_edgar(SUB), Reader([]), None)
        assert "no SEC CIK" in run.errors[0]
        co2 = add_company(s)
        r2 = weekly.process_company(s, co2, fake_edgar(SUB), Reader(facts()), Reader(facts()))
        assert r2.card_status.startswith("no price")


def test_run_weekly_isolates_companies_and_logs_the_job(engine):
    with db.session_scope(engine) as s:
        add_company(s)
        s.add(db.Company(name="Mystery", ticker="ZZZZ", stage="explorer", currency="USD", primary_metals=""))
    out = weekly.run_weekly(engine, fake_edgar(SUB), Reader(facts()), Reader(facts()))
    assert out["companies"] == 2 and out["docs"] == 1 and out["errors"] == 1
    with db.session_scope(engine) as s:
        job = s.scalars(select(db.JobRun)).one()
        assert job.finished_at is not None and json.loads(job.summary_json)["companies"] == 2


# ---------------------------------------------------------------- stages, prices, universe
def fb(*keys, **vals):
    return FactBook([F(k, value_num=1.0, value_text=vals.get(k), metal="au", status="verified") for k in keys])


def test_stage_inference_and_manual_stage_is_never_changed():
    assert stages.infer(fb("production_ttm", "aisc_per_unit"))[0] == "producer"
    assert stages.infer(fb("royalty_revenue_ttm", "portfolio_asset_count"))[0] == "royalty"
    assert stages.infer(fb("npv_after_tax", "resource_contained"))[0] == "developer"
    assert stages.infer(FactBook([F("study_stage", value_text="PFS")]))[0] == "developer"
    assert stages.infer(fb("resource_contained"))[0] == "explorer" and stages.infer(fb())[0] is None
    co = NS(stage="developer", stage_auto=False, primary_metals="ag")
    assert stages.apply(None, co, fb("production_ttm")) == {"changed": False} and co.stage == "developer"


def test_prices_fx_and_bank_of_canada_parser(engine):
    with db.session_scope(engine) as s:
        prices.set_price(s, "dml.to", 3.37, "cad", date(2026, 10, 8))
        prices.set_price(s, "DML.TO", 3.40, "CAD", date(2026, 10, 9))
        assert prices.latest_price(s, "DML.TO").price == 3.40 and prices.latest_price(s, "NOPE") is None
        prices.set_fx(s, "CAD", 0.70, date(2026, 10, 1)); prices.set_fx(s, "CAD", 0.7019, date(2026, 10, 2))
        assert prices.latest_fx(s) == {"USD": 1.0, "CAD": 0.7019}
    d, rate = prices.parse_boc(json.dumps({"observations": [{"d": "2026-10-09", "FXUSDCAD": {"v": "1.4000"}}]}))
    assert d == date(2026, 10, 9) and rate == pytest.approx(1 / 1.4)
    with pytest.raises(ValueError, match="unexpected Bank of Canada"):
        prices.parse_boc('{"oops": 1}')
    assert prices.fetch_boc(lambda u: json.dumps({"observations": [{"d": "2026-10-09", "FXUSDCAD": {"v": "1.25"}}]}).encode())[1] == pytest.approx(0.8)


def test_universe_scan_keeps_mining_companies_and_resumes_from_cache(engine, tmp_path, monkeypatch):
    from headgrade import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    calls = []
    sics = {"0000000001": "1040", "0000000002": "1090", "0000000003": "6770"}

    def getter(url):
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"cik_str": 1, "ticker": "GOLD1", "title": "Gold One"}, "1": {"cik_str": 1, "ticker": "GOLD1.B", "title": "Gold One"},
                               "2": {"cik_str": 2, "ticker": "URAN", "title": "Uranium Co"}, "3": {"cik_str": 3, "ticker": "SPAC", "title": "A SPAC"}}).encode()
        calls.append(url)
        cik = url.rsplit("CIK", 1)[1].split(".")[0]
        return json.dumps({"sic": sics[cik], "name": "N" + cik[-1], "exchanges": ["NYSE"]}).encode()
    edgar = Edgar(getter=getter, min_interval=0)
    with db.session_scope(engine) as s:
        r = universe.scan_sec(edgar, s, limit=2)
        assert r["scanned_now"] == 2 and r["added"] == 2 and len(calls) == 2           # SPAC not reached yet; duplicate ticker for the same CIK skipped
        r2 = universe.scan_sec(Edgar(getter=getter, min_interval=0), s)                  # resumes: only the third company is looked up
        assert r2["scanned_now"] == 1 and r2["added"] == 0 and len(calls) == 3 and r2["mining_matched"] == 2
        cos = list(s.scalars(select(db.Company)))
        assert len(cos) == 2 and all(c.stage_auto and c.cik for c in cos)


def test_dry_run_lists_documents_and_reads_nothing(engine):
    with db.session_scope(engine) as s:
        co = add_company(s)
        a, b = Reader(facts()), Reader(facts())
        run = weekly.process_company(s, co, fake_edgar(SUB), a, b, dry_run=True)
        assert run.filings_new == 1 and run.docs == 1 and "exhibit991.htm" in run.preview[0]
        assert (a.calls, b.calls) == (0, 0)
        assert not list(s.scalars(select(db.Fact))) and not list(s.scalars(select(db.Filing))) and co.last_filing_seen is None


def test_document_cap_stops_and_next_run_continues(engine):
    with db.session_scope(engine) as s:
        co = add_company(s)
        sub = json.loads(json.dumps(SUB))
        rec = sub["filings"]["recent"]
        for k, v in (("accessionNumber", "0001429937-26-000050"), ("form", "6-K"), ("filingDate", "2026-09-10"), ("reportDate", "2026-06-30"),
                     ("primaryDocument", "form6-k.htm"), ("primaryDocDescription", "6-K")):
            rec[k].append(v)
        edgar, a = fake_edgar(sub), Reader(facts())
        r1 = weekly.process_company(s, co, edgar, a, None, build=False, budget={"docs_left": 1})
        assert r1.docs == 1 and r1.stopped_by_cap and len(list(s.scalars(select(db.Filing)))) == 1
        r2 = weekly.process_company(s, co, edgar, a, None, build=False, budget={"docs_left": 5})
        assert r2.filings_new == 1 and r2.docs == 1 and not r2.stopped_by_cap
