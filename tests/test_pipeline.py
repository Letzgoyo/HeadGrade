import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.demo_data import build
from minecards import db, pipeline
from minecards.render import render_html
from minecards.valuation import Market
from sqlalchemy import select


@pytest.fixture
def world():
    engine = db.make_engine("sqlite:///:memory:")
    db.init_db(engine)
    with db.session_scope(engine) as s:
        yield s


def market(price=2.5):
    return Market(price, "USD", date(2026, 10, 9))


def test_end_to_end_card_for_a_fictional_developer(world):
    co = build(world)
    deck = pipeline.latest_deck(world)
    card = pipeline.build_card(world, co, deck, market(), publish=True, today=date(2026, 10, 9))
    g = card["grade"]
    assert card["mode"] == "published" and g["quality_grade"] != "n/a" and g["value_grade"] != "n/a"
    assert g["value_score"] is not None and g["quadrant"]
    assert g["confidence"]["verified_share"] == 1.0
    assert card["valuation"]["metrics"]["p_nav"] < 0.2
    assert card["disclosures"]["owner_holds_position"] is True
    labels = {e["label"] for e in card["evidence"]}
    assert any("after-tax NPV" in x for x in labels) and all(e["page"] for e in card["evidence"])
    json.loads(pipeline.card_json(card))                      # fully serialisable


def test_publish_mode_ignores_unverified_facts_but_draft_uses_them(world):
    co = build(world, verified=False)
    deck = pipeline.latest_deck(world)
    draft = pipeline.build_card(world, co, deck, market(), publish=False)
    pub = pipeline.build_card(world, co, deck, market(), publish=True)
    assert draft["grade"]["value_score"] is not None            # drafts work from unverified facts
    assert pub["grade"]["value_score"] is None and pub["grade"]["quality_score"] is None   # nothing verified yet: nothing published
    assert draft["grade"]["confidence"]["verified_share"] == 0.0


def test_verifying_facts_moves_a_card_toward_publishable(world):
    co = build(world, verified=False)
    deck = pipeline.latest_deck(world)
    for f in world.scalars(select(db.Fact)):
        f.status = "verified"
    world.flush()
    pub = pipeline.build_card(world, co, deck, market(), publish=True)
    assert pub["grade"]["value_score"] is not None


def test_weekly_diff_reports_material_changes(world):
    co = build(world)
    deck = pipeline.latest_deck(world)
    old = pipeline.build_card(world, co, deck, market(2.5), publish=True)
    pipeline.save_snapshot(world, co, old)
    assert pipeline.diff_cards(None, old) == ["First card for this company."]
    # share price doubles and the deck falls
    from minecards.decks import Deck
    new = pipeline.build_card(world, co, Deck("spot", {"au": 2600.0, "ag": 30.0}, date(2026, 10, 16)), market(5.0), publish=True)
    changes = pipeline.diff_cards(pipeline.last_snapshot(world, co), new)
    assert any("metal price deck" in c for c in changes) and any("Share price moved" in c for c in changes)
    assert any("Price-to-NAV" in c for c in changes)
    assert pipeline.diff_cards(old, old) == ["No material changes."]


def test_html_renders_key_sections_and_escapes_document_text(world):
    co = build(world)
    f = world.scalars(select(db.Fact).where(db.Fact.key == "team.prior_mines_built")).one()
    f.quote = "<script>alert('x')</script> management has built three previous mines"
    world.flush()
    card = pipeline.build_card(world, co, pipeline.latest_deck(world), market(), publish=True)
    html = render_html(card, ["Example change"])
    for needle in ("Example Gold Corp", "Quality", "Valuation", "Price / NAV", "Sensitivity", "Sources", "Funding gap", "holds a position", "not personal financial advice"):
        assert needle in html, needle
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    draft = render_html(pipeline.build_card(world, co, pipeline.latest_deck(world), market(), publish=False))
    assert "DRAFT" in draft and "DRAFT" not in html
