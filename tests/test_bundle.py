import json
from datetime import date

import pytest
from sqlalchemy import select

from headgrade import bundle, db, review
from headgrade.edgar import html_to_pages

HTML = ("<html><body><p>Example Gold Corp results</p><p>Cash and cash equivalents were $287 million at June 30, 2026.</p>"
        "<p>All-in sustaining costs were $2,356 per ounce sold.</p>" + "<p>Operations continued as planned. </p>" * 80 + "</body></html>")


def fact(doc, key, quote, **kw):
    return {"doc": doc, "key": key, "quote": quote, "page": 1, **kw}


A = [fact("d1", "cash", "Cash and cash equivalents were $287 million at June 30, 2026", value=287, unit="US$ millions", as_of="2026-06-30"),
     fact("d1", "aisc_per_unit", "All-in sustaining costs were $2,356 per ounce sold", metal="au", value=2356, unit="US$/oz", as_of="2026-06-30"),
     fact("d1", "revenue_ttm", "this sentence is not in the document at all 999", value=999, unit="US$ millions")]
B = [fact("d1", "cash", "Cash and cash equivalents were $287 million at June 30, 2026", value=287, unit="US$ millions", as_of="2026-06-30"),
     fact("d1", "aisc_per_unit", "All-in sustaining costs were $2,356 per ounce sold", metal="au", value=2365, unit="US$/oz", as_of="2026-06-30")]


def make():
    return bundle.build_bundle({"name": "Example Gold Corp", "ticker": "EGC", "currency": "USD", "primary_metals": "au"},
                               [{"id": "d1", "title": "Q2 results", "kind": "press_release", "effective_date": "2026-06-30", "pages": html_to_pages(HTML)}],
                               A, B, price={"price": 5.0, "currency": "USD", "as_of": "2026-10-09"}, deck={"as_of": "2026-10-09", "prices": {"au": 4367.0}})


@pytest.fixture
def session(tmp_path):
    e = db.make_engine(f"sqlite:///{tmp_path / 'b.db'}")
    db.init_db(e)
    with db.session_scope(e) as s:
        yield s


def test_bundle_is_small_and_json_roundtrips():
    b = make()
    assert json.loads(json.dumps(b))["documents"][0]["n_pages"] == len(html_to_pages(HTML)) and set(b["documents"][0]["pages"]) <= {"1", "2", "3", "0"}


def test_import_rechecks_quotes_runs_auto_verification_and_builds_a_published_card(session):
    out = bundle.import_bundle(session, make(), today=date(2026, 10, 11))
    assert (out["facts"], out["rejected"]) == (3, 1)                 # the invented quote is rejected in code
    assert out["auto_verified"] == 1 and out["needs_review"] == 1     # cash agrees; AISC 2356 vs 2365 conflicts
    by = {f.key: f for f in session.scalars(select(db.Fact))}
    assert by["cash"].verified_by == "auto" and by["aisc_per_unit"].status == "unverified" and by["revenue_ttm"].status == "rejected"
    assert out["card"] == "ok" and review.queue(session, None, needs_attention=True)[0].key == "aisc_per_unit"
    again = bundle.import_bundle(session, make(), build=False)       # idempotent: same document is not read twice
    assert again["facts"] == 0
