import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.demo_data import build
from minecards import db, review
from sqlalchemy import select


@pytest.fixture
def world():
    engine = db.make_engine("sqlite:///:memory:")
    db.init_db(engine)
    with db.session_scope(engine) as s:
        co = build(s, verified=False)
        yield s, co


def test_queue_puts_critical_inputs_first_and_filters(world):
    s, co = world
    rows = review.queue(s, co.id)
    assert rows[0].key in review.CRITICAL_KEYS
    assert all(r.key in review.CRITICAL_KEYS for r in review.queue(s, co.id, critical_only=True))
    assert all("npv" in r.key for r in review.queue(s, co.id, key_contains="npv"))
    p = review.progress(s, co.id)
    assert p["total"] == p["unverified"] and p["critical_unreviewed"] > 0


def test_verify_reject_and_override_are_recorded_and_reversible(world):
    s, co = world
    f = s.scalars(select(db.Fact).where(db.Fact.key == "npv_after_tax")).one()
    review.set_status(s, f.id, "verified")
    assert f.status == "verified" and f.verified_at is not None
    review.set_status(s, f.id, "unverified")
    assert f.verified_at is None
    review.override(s, f.id, value=1.75e9, note="per Table 22-1")
    assert f.status == "override" and f.value_num == 1.75e9 and "extracted value was 1800000000.0" in f.note
    review.override(s, f.id, text="PFS")
    assert f.value_text == "PFS"


def test_page_text_returns_the_cited_page(world):
    s, co = world
    import json
    doc = s.scalars(select(db.Document).where(db.Document.kind == "technical_report")).one()
    doc.pages_json = json.dumps([f"page {i} text" for i in range(1, 30)])
    f = s.scalars(select(db.Fact).where(db.Fact.key == "npv_after_tax")).one()
    f.page = 12
    s.flush()
    assert review.page_text(s, f) == "--- page 12 ---\npage 12 text"
    assert "page 11 text" in review.page_text(s, f, context=1)


def test_review_app_pages_render_and_verify_button_works(tmp_path, monkeypatch):
    monkeypatch.setenv("MINECARDS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    for m in [k for k in sys.modules if k.startswith("minecards")]:
        del sys.modules[m]
    from minecards import db as db2
    from streamlit.testing.v1 import AppTest
    eng = db2.make_engine()
    db2.init_db(eng)
    with db2.session_scope(eng) as s:
        from examples.demo_data import build as b2
        b2(s, verified=False)
    app = str(Path(__file__).resolve().parent.parent / "review_app.py")
    for page in ("Review queue", "Card preview", "Companies & documents", "Decks & benchmarks"):
        at = AppTest.from_file(app, default_timeout=60)
        at.run()
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])
    at = AppTest.from_file(app, default_timeout=60)
    at.run()
    verify = [b for b in at.button if b.label == "Verify"]
    assert verify
    verify[0].click()
    at.run()
    assert not at.exception
    with db2.session_scope(eng) as s:
        assert s.scalar(select(db2.Fact).where(db2.Fact.status == "verified")) is not None
