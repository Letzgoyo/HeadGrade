import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headgrade import edgar
from headgrade.edgar import Edgar, SecError, classify, html_to_pages

SUBMISSIONS = {
    "cik": "1429937", "name": "B2GOLD CORP", "sic": "1040", "tickers": ["BTG"],
    "filings": {"recent": {
        "accessionNumber": ["0001429937-26-000010", "0001429937-26-000007", "0001429937-26-000099", "0001429937-25-000004"],
        "form": ["6-K", "6-K", "SC 13G", "40-F"],
        "filingDate": ["2026-08-06", "2026-05-06", "2026-05-07", "2026-03-30"],
        "reportDate": ["2026-06-30", "2026-03-31", "", "2025-12-31"],
        "primaryDocument": ["form6-k.htm", "form6-k.htm", "x.htm", "form40-f.htm"],
        "primaryDocDescription": ["6-K", "6-K", "13G", "40-F"],
    }},
}

RESULTS_HTML = """<html><body><p style="page-break-before: always">B2GOLD CORP. REPORTS Q2 2026 RESULTS</p>
<p>News release. Gold revenue for the second quarter was strong; production for the quarter was 203,648 ounces and the year ended guidance is unchanged.</p>
<script>var x = 1;</script><table>
<tr><td>Gold revenue ($ in thousands)</td><td>$</td><td>789,000</td><td>$</td><td>692,000</td></tr>
<tr><td>Free cash flow</td><td>$</td><td>(258</td><td>)</td><td>12.5</td><td>%</td></tr></table>
<hr><p>""" + "Mineral resource and production update. " * 60 + """</p></body></html>"""


def fake_world():
    calls = []

    def getter(url):
        calls.append(url)
        if url.endswith("company_tickers.json"):
            return json.dumps({"0": {"cik_str": 1429937, "ticker": "BTG", "title": "B2GOLD CORP"}}).encode()
        if "submissions/CIK0001429937.json" in url:
            return json.dumps(SUBMISSIONS).encode()
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "form6-k.htm"}, {"name": "exhibit991.htm"}, {"name": "R2.htm"}, {"name": "logo.jpg"},
                                                      {"name": "0001429937-26-000010-index.htm"}, {"name": "exhibit992.pdf"}]}}).encode()
        if url.endswith("exhibit991.htm"):
            return RESULTS_HTML.encode()
        if url.endswith("form6-k.htm"):
            return b"<html><body><p>FORM 6-K cover page</p></body></html>"
        raise AssertionError("unexpected url " + url)
    return Edgar(getter=getter, min_interval=0), calls


def test_user_agent_is_required_for_real_requests(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecError, match="SEC_USER_AGENT"):
        Edgar()
    assert Edgar(user_agent="Jane Doe jane@example.com").ua.startswith("Jane")


def test_ticker_lookup_and_filing_list_filters_forms_and_dates():
    e, calls = fake_world()
    assert e.cik_for_ticker("BTG") == "0001429937" and e.cik_for_ticker("btg.to") == "0001429937" and e.cik_for_ticker("ZZZ") is None
    refs = e.recent_filings("0001429937", since=date(2026, 4, 1))
    assert [r.accession for r in refs] == ["0001429937-26-000007", "0001429937-26-000010"]       # oldest first; the 13G (wrong form) and the March 40-F (too old) are dropped
    assert refs[1].period == date(2026, 6, 30) and refs[1].nodash == "000142993726000010"
    allr = e.recent_filings("0001429937")
    assert [r.form for r in allr] == ["40-F", "6-K", "6-K"]
    assert e.doc_url("0001429937", "0001429937-26-000010", "a.htm") == "https://www.sec.gov/Archives/edgar/data/1429937/000142993726000010/a.htm"


def test_html_to_pages_keeps_table_rows_and_drops_scripts():
    pages = html_to_pages(RESULTS_HTML)
    text = "\n".join(pages)
    assert "var x" not in text
    assert "Gold revenue ($ in thousands) | 789,000 | 692,000" in text                 # '$' cells dropped, columns joined
    assert "Free cash flow | (258) | 12.5%" in text                                     # ')' and '%' re-attached to their numbers
    assert len(pages) >= 2 and all(len(p) <= 8000 for p in pages)                       # page-break marker and chunking
    assert "REPORTS Q2 2026 RESULTS" in pages[0]


def test_classification_rules():
    assert classify("short cover page") is None
    tech = "NI 43-101 Technical Report on the Alpha Project\n" + "x" * 2000
    assert classify(tech) == "technical_report"
    assert classify("ANNUAL INFORMATION FORM\n" + "x" * 2000) == "aif"
    assert classify("Management's Discussion and Analysis for the quarter\n" + "x" * 2000) == "mdna"
    fin = "Condensed Consolidated Interim Financial Statements\nStatements of Financial Position\n" + "x" * 1500 + "\nStatements of Cash Flows"
    assert classify(fin) == "financials"
    assert classify("Acme Reports Q2 2026 Results\n" + "The quarter was strong. " * 80) == "press_release"
    assert classify("Notice of annual meeting. " * 100) is None


def test_fetch_filing_documents_skips_noise_and_classifies():
    e, calls = fake_world()
    ref = e.recent_filings("0001429937", since=date(2026, 8, 1))[0]
    docs = []
    # the pdf exhibit in the fake index is not served by the fake getter: mimic a PDF that fails to parse
    orig = e._getter

    def getter(url):
        if url.endswith("exhibit992.pdf"):
            return b"not a pdf"
        return orig(url)
    e._getter = getter
    docs = edgar.fetch_filing_documents(e, "0001429937", ref)
    assert [d.name for d in docs] == ["exhibit991.htm"] and docs[0].kind == "press_release"
    assert docs[0].url.endswith("/000142993726000010/exhibit991.htm")
    assert not any(u.endswith(("R2.htm", "logo.jpg")) for u in calls)                  # XBRL pages and images never fetched


def test_throttle_spaces_requests():
    sleeps, t = [], [0.0]
    e = Edgar(getter=lambda u: b"{}", min_interval=0.5, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    e.get("a"); e.get("b")
    assert sleeps and sleeps[0] == pytest.approx(0.5)
