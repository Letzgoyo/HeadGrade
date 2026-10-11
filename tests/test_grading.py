from datetime import date

import pytest

from minecards import grading
from minecards.decks import Deck
from minecards.facts import F, FactBook
from minecards.valuation import Market, Proj, value_developer, value_explorer
from test_valuation import DECK, P1, developer_facts, explorer_facts, f


def test_letters():
    assert [grading.letter(x) for x in (95, 80, 79.9, 65, 50, 35, 10, None)] == ["A", "A", "B", "B", "C", "D", "F", "n/a"]


def dev_world(**extra):
    fb = developer_facts()
    fb.facts += [
        f("irr_after_tax", 24, project_id=1), f("permit_stage", text="approved", project_id=1),
        f("jurisdiction", text="Nevada", project_id=1),
        f("team.prior_mines_built", 3), f("team.insider_ownership_pct", 8),
    ] + [x for x in extra.get("facts", [])]
    val = value_developer(fb, Market(2.50), [P1], DECK)
    return fb, val


def test_developer_grade_separates_quality_from_value():
    fb, val = dev_world()
    g = grading.grade(fb, "developer", [P1], val, {"Nevada": 90.0}, today=date(2026, 10, 9))
    cats = g["categories"]
    assert cats["team"]["score"] is not None and cats["asset"]["score"] is not None
    assert cats["jurisdiction"]["score"] == pytest.approx(0.6 * 90 + 0.4 * 85)
    assert cats["growth"]["score"] is None and any("not scored" in n for n in cats["growth"]["notes"])
    # the funding gap (900M capex vs 80M cash vs 500M market cap) must drag financial strength to the floor and raise a flag
    assert cats["financial"]["score"] == pytest.approx(0.0)
    assert any(r["severity"] == "high" and "dilute" in r["text"] for r in g["red_flags"])
    # quality and value are independent numbers
    assert g["value_score"] == val["value_score"] and g["quality_score"] != g["value_score"]


def test_missing_data_lowers_confidence_and_blocks_a_quality_grade():
    fb = explorer_facts()
    val = value_explorer(fb, Market(0.50), [P1], DECK, [])
    g = grading.grade(fb, "explorer", [P1], val, {})
    assert g["quality_score"] is None and g["quadrant"] == "Not enough data to place"
    assert g["confidence"]["level"] == "low"


def test_regulatory_action_reduces_team_score_and_unknown_jurisdiction_is_noted():
    base, v1 = dev_world()
    g1 = grading.grade(base, "developer", [P1], v1, {})
    fb2, v2 = dev_world(facts=[f("team.regulatory_actions", 2)])
    g2 = grading.grade(fb2, "developer", [P1], v2, {})
    assert g2["categories"]["team"]["score"] == pytest.approx(max(0, g1["categories"]["team"]["score"] - 50))
    assert any("not in the jurisdiction table" in n for n in g1["categories"]["jurisdiction"]["notes"])
    assert any("regulatory" in r["text"].lower() for r in g2["red_flags"])


def test_share_dilution_history_is_scored_from_dated_share_counts():
    fb, val = dev_world(facts=[F("shares_basic", 100e6, as_of=date(2023, 9, 30), status="verified"),
                               F("shares_basic", 200e6, as_of=date(2026, 9, 30), status="verified")])
    notes = []
    s = grading._team(fb, notes)
    assert any("share count grew" in n for n in notes)       # ~26% a year
    assert s is not None
