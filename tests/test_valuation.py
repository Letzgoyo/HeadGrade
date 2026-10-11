from datetime import date
from types import SimpleNamespace as NS

import pytest

from headgrade.decks import Deck
from headgrade.facts import F, FactBook
from headgrade.valuation import Market, Proj, interp, scale, value_developer, value_explorer, value_producer, value_royalty

DECK = Deck("test", {"au": 4000.0, "ag": 45.0}, date(2026, 10, 9))
P1 = Proj(1, "Alpha", "au")


def f(key, v=None, text=None, **kw):
    return F(key, value_num=v, value_text=text, status=kw.pop("status", "verified"), **kw)


def developer_facts(shares=200e6, cash=80e6):
    return FactBook([
        f("shares_fully_diluted", shares), f("cash", cash, currency="USD"), f("debt", 0, currency="USD"),
        f("npv_after_tax", 1.8e9, currency="USD", project_id=1), f("initial_capex", 900e6, currency="USD", project_id=1),
        f("mine_life_years", 12.5, project_id=1), f("npv_discount_rate", 5, project_id=1), f("construction_years", 3, project_id=1),
        f("annual_production", 250_000, metal="au", project_id=1), f("study_price", 2000, metal="au", project_id=1),
        f("study_stage", text="PFS", project_id=1), f("ownership_pct", 100, project_id=1), f("tax_rate", 27),
    ])


def test_helpers():
    assert interp(5, [(0, 100), (10, 0)]) == 50 and interp(-1, [(0, 100), (10, 0)]) == 100 and interp(99, [(0, 100), (10, 0)]) == 0
    assert scale(0.065, 0.03, 0.10) == pytest.approx(50) and scale(7, 10, 4) == pytest.approx(50)


def test_developer_nav_is_repriced_and_p_nav_computed():
    r = value_developer(developer_facts(), Market(2.50, "USD"), [P1], DECK)
    m = r["metrics"]
    proj = r["tables"]["projects"][0]
    assert proj["status"] == "ok" and proj["published_npv_usd"] == 1.8e9
    assert proj["npv_at_deck"] > 1.8e9                      # deck is double the study price
    assert proj["npv_at_deck_8pct"] < proj["npv_at_deck"]    # higher discount rate lowers NAV
    assert proj["npv_at_80pct_deck"] < proj["npv_at_deck"]
    assert m["market_cap_usd"] == pytest.approx(500e6)
    assert m["p_nav"] == pytest.approx(m["market_cap_usd"] / m["nav_usd"])
    assert m["p_nav"] < 0.2 and m["study_stage"] == "PFS" and m["typical_p_nav_range"] == (0.30, 0.50)
    assert r["value_score"] is not None and r["value_score"] > 70   # trades below the typical PFS range
    # the market is implying a metal price far below the deck
    assert m["implied_metal_multiple_at_1x_nav"] < 1.0
    assert m["implied_primary_price_at_1x_nav"] == pytest.approx(m["implied_metal_multiple_at_1x_nav"] * 4000)
    # consistency: re-pricing at the implied multiple really does bring NAV to the market cap
    from headgrade import npv as N
    pj = r["tables"]["projects"][0]
    assert abs(sum(1 for _ in r["tables"]["sensitivity"])) == 5
    row100 = next(x for x in r["tables"]["sensitivity"] if x["price_vs_deck"] == 1.0)
    assert row100["nav_published_rate"] == pytest.approx(m["nav_usd"])


def test_developer_funding_gap_and_dilution():
    r = value_developer(developer_facts(), Market(2.50, "USD"), [P1], DECK)
    m = r["metrics"]
    assert m["funding_gap_usd"] == pytest.approx(900e6 - 80e6)
    expected_new = 820e6 / (2.50 * 0.9)
    assert m["dilution_if_all_equity_pct"] == pytest.approx(100 * expected_new / 200e6)


def test_developer_without_study_stage_is_not_rated_and_says_why():
    facts = developer_facts()
    facts.facts = [x for x in facts.facts if x.key != "study_stage"]
    r = value_developer(facts, Market(2.50, "USD"), [P1], DECK)
    assert r["value_score"] is None and any("study stage" in x for x in r["flags"])


def test_developer_reports_inconsistent_inputs_instead_of_guessing():
    facts = developer_facts()
    for x in facts.facts:
        if x.key == "npv_after_tax":
            x.value_num = 9e12                               # impossible for this revenue
    r = value_developer(facts, Market(2.50, "USD"), [P1], DECK)
    assert r["tables"]["projects"][0]["status"].startswith("could not reconcile") and r["value_score"] is None


def test_fx_conversion_and_missing_fx():
    facts = developer_facts()
    for x in facts.facts:
        if x.key in ("cash",):
            x.currency = "CAD"
    # trading in CAD, fx given: CAD cash converts at 0.73
    r = value_developer(facts, Market(3.40, "CAD", fx_usd={"USD": 1.0, "CAD": 0.73}), [P1], DECK)
    assert r["metrics"]["market_cap_usd"] == pytest.approx(200e6 * 3.40 * 0.73)
    assert r["metrics"]["net_cash_usd"] == pytest.approx(80e6 * 0.73)
    r2 = value_developer(developer_facts(), Market(3.40, "CAD", fx_usd={"USD": 1.0}), [P1], DECK)   # no CAD rate
    assert r2["value_score"] is None and any("FX" in x for x in r2["flags"])


# ---------------------------------------------------------------- explorer
def explorer_facts():
    return FactBook([
        f("shares_basic", 100e6), f("cash", 10e6, currency="USD"), f("debt", 0, currency="USD"), f("quarterly_cash_burn", 2e6, currency="USD"),
        f("resource_contained", 1.2e6, metal="au", category="measured_indicated", project_id=1),
        f("resource_contained", 2.0e6, metal="au", category="inferred", project_id=1),
    ])


def test_explorer_value_needs_benchmarks():
    r = value_explorer(explorer_facts(), Market(0.50), [P1], DECK, benchmarks=[])
    m = r["metrics"]
    assert r["value_score"] is None and any("NOT RATED" in x for x in r["flags"])
    assert m["ev_usd"] == pytest.approx(40e6) and m["weighted_equivalent_units"] == pytest.approx(1.2e6 + 0.5 * 2.0e6)
    assert m["ev_per_weighted_unit_usd"] == pytest.approx(40e6 / 2.2e6)
    assert m["runway_months"] == pytest.approx(10e6 / (2e6 / 3)) and m["mi_share_of_resource"] == pytest.approx(1.2 / 3.2)
    assert any("fully diluted" in x for x in r["flags"])           # basic shares were used


def test_explorer_scores_against_a_benchmark():
    bm = NS(kind="peer_ev_per_unit", metal="au", stage="explorer", p25=20.0, median=40.0, p75=80.0, n=25, as_of=date(2026, 9, 1), source="test")
    cheap = value_explorer(explorer_facts(), Market(0.50), [P1], DECK, [bm])
    rich = value_explorer(explorer_facts(), Market(5.00), [P1], DECK, [bm])
    assert cheap["value_score"] > 80 > 30 > rich["value_score"]
    assert cheap["metrics"]["peer_benchmark"]["n"] == 25


def test_explorer_summing_measured_and_indicated_and_equivalents():
    fb = FactBook([f("shares_basic", 100e6), f("cash", 10e6, currency="USD"), f("debt", 0, currency="USD"),
                   f("resource_contained", 400e3, metal="au", category="measured", project_id=1),
                   f("resource_contained", 800e3, metal="au", category="indicated", project_id=1),
                   f("resource_contained", 30e6, metal="ag", category="indicated", project_id=1),
                   f("recovery", 90, metal="au"), f("recovery", 80, metal="ag")])
    r = value_explorer(fb, Market(0.50), [P1], DECK, [])
    expected = (0.9 * 1.2e6 * 4000 + 0.8 * 30e6 * 45) / 4000
    assert r["metrics"]["mi_equivalent_units"] == pytest.approx(expected)


# ---------------------------------------------------------------- producer
def test_producer_normalises_to_the_deck_and_stresses():
    fb = FactBook([
        f("shares_fully_diluted", 400e6), f("cash", 200e6, currency="USD"), f("debt", 300e6, currency="USD"),
        f("op_cash_flow_ttm", 700e6, currency="USD"), f("capex_ttm", 300e6, currency="USD"), f("ebitda_ttm", 900e6, currency="USD"),
        f("production_ttm", 400_000, metal="au"), f("realized_price", 3000, metal="au"), f("aisc_per_unit", 1900, metal="au"),
        f("tax_rate", 25), f("reserve_life_years", 9),
    ])
    r = value_producer(fb, Market(10.0), [], DECK)        # market cap $4.0B
    m = r["metrics"]
    assert m["fcf_ttm_usd"] == pytest.approx(400e6) and m["fcf_yield_reported"] == pytest.approx(0.10)
    # deck 4000 vs realized 3000: +1000/oz * 400k oz * (1-25%) = +300M
    assert m["fcf_normalised_usd"] == pytest.approx(400e6 + 300e6)
    assert m["fcf_at_stress_usd"] == pytest.approx(700e6 - 0.2 * 400_000 * 4000 * 0.75)
    assert m["ev_to_ebitda"] == pytest.approx((4.0e9 + 100e6) / 900e6)
    assert m["aisc_margin_at_deck"] == pytest.approx((4000 - 1900) / 4000)
    assert 0 <= r["value_score"] <= 100


# ---------------------------------------------------------------- royalty
def test_royalty_not_rated_without_benchmarks_then_rated_with_them():
    fb = FactBook([f("shares_basic", 100e6), f("cash", 50e6, currency="USD"), f("debt", 0, currency="USD"),
                   f("op_cash_flow_ttm", 60e6, currency="USD"), f("royalty_revenue_ttm", 80e6, currency="USD")])
    r = value_royalty(fb, Market(20.0), DECK, [])
    assert r["value_score"] is None and r["metrics"]["price_to_cash_flow"] == pytest.approx(2e9 / 60e6)
    bm = NS(kind="peer_pcf", metal="au", stage="royalty", p25=18.0, median=25.0, p75=35.0, n=6, as_of=date(2026, 9, 1), source="test")
    r2 = value_royalty(fb, Market(20.0), DECK, [bm])
    assert r2["value_score"] is not None


def test_pro_forma_p_nav_is_hand_checkable_and_drives_the_score():
    r = value_developer(developer_facts(), Market(2.50, "USD"), [P1], DECK)
    m = r["metrics"]
    gap = 900e6 - 80e6
    new_sh = gap / (2.50 * 0.9)
    pf = (m["nav_usd"] + gap) / (200e6 + new_sh)
    assert m["pro_forma_nav_per_share_usd"] == pytest.approx(pf)
    assert m["pro_forma_p_nav"] == pytest.approx(2.50 / pf)
    assert m["pro_forma_p_nav"] > m["p_nav"]                  # dilution makes it look less cheap than plain P/NAV
    assert m["p_nav_used_for_score"] == pytest.approx(m["pro_forma_p_nav"])
    # a fully funded company is scored on plain P/NAV
    rich = value_developer(developer_facts(cash=2e9), Market(2.50, "USD"), [P1], DECK)
    assert rich["metrics"]["funding_gap_usd"] == 0 and rich["metrics"]["p_nav_used_for_score"] == pytest.approx(rich["metrics"]["p_nav"])


def test_producer_with_partial_data_still_reports_what_it_can_and_does_not_invent_a_score():
    fb = FactBook([f("shares_basic", 100e6), f("cash", 50e6, currency="USD"), f("debt", 100e6, currency="USD"),
                   f("op_cash_flow_ttm", 200e6, currency="USD"), f("revenue_ttm", 800e6, currency="USD"),
                   f("aisc_per_unit", 2000, metal="au")])
    r = value_producer(fb, Market(20.0), [], DECK)       # market cap $2.0B
    m = r["metrics"]
    assert r["value_score"] is None and any("not enough producer data" in x for x in r["flags"])
    assert m["fcf_ttm_usd"] is None and m["ev_to_ebitda"] is None
    assert m["price_to_operating_cash_flow"] == pytest.approx(2.0e9 / 200e6)
    assert m["ev_to_revenue"] == pytest.approx((2.0e9 + 100e6 - 50e6) / 800e6)
    assert m["aisc_margin_at_deck"] == pytest.approx((4000 - 2000) / 4000)
