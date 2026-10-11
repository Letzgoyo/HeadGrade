import pytest

from headgrade import decks, equiv, npv
from headgrade.npv import ProjectInputs


def simple():
    # 1 construction year, 2 operating years, capex 100, revenue 100/yr, no tax/royalty, r = 10%
    return ProjectInputs({"au": 1.0}, mine_life=2, construction_years=1, initial_capex=100, tax_rate=0.0, royalty_pct=0.0, discount_rate=0.10)


def test_npv_matches_hand_calculation():
    # -100/1.1 + 80/1.21 + 80/1.331 = 35.3122...
    assert npv.npv(simple(), {"au": 100.0}, annual_opex=20.0) == pytest.approx(35.3122, abs=1e-3)


def test_calibration_recovers_the_hidden_cost():
    inp = simple()
    target = npv.npv(inp, {"au": 100.0}, 20.0)
    x, why = npv.calibrate_opex(inp, {"au": 100.0}, target)
    assert why == "ok" and x == pytest.approx(20.0, abs=1e-6)


def test_calibration_with_tax_and_royalty_roundtrips():
    inp = ProjectInputs({"au": 250_000, "ag": 2_000_000}, mine_life=12.5, construction_years=3, initial_capex=900e6,
                        tax_rate=0.27, royalty_pct=0.02, discount_rate=0.05)
    study = {"au": 2000.0, "ag": 24.0}
    published = 1.8e9
    x, why = npv.calibrate_opex(inp, study, published)
    assert why == "ok" and x > 0
    assert npv.npv(inp, study, x) == pytest.approx(published, rel=1e-6)
    # higher prices raise NPV; higher discount rate lowers it
    assert npv.npv(inp, {"au": 3000.0, "ag": 36.0}, x) > published > npv.npv(inp, study, x, rate=0.08)


def test_inconsistent_inputs_are_reported_not_hidden():
    inp = simple()
    x, why = npv.calibrate_opex(inp, {"au": 100.0}, 10_000.0)   # published NPV impossible for this revenue
    assert x is None and "higher than the model can reach" in why


def test_implied_price_multiplier_linear_case():
    inp = simple()
    x = 20.0
    base = {"au": 100.0}
    # NAV(m) = ownership * NPV(100*m) + cash. Market cap equal to NAV at m=1.0 must give m=1.0
    nav1 = npv.nav_equity(inp, x, base, 1.0, 0.0)
    m, why = npv.implied_price_multiplier(inp, x, base, 1.0, 0.0, market_cap=nav1)
    assert why == "ok" and m == pytest.approx(1.0, abs=1e-6)
    # a market cap at 40% of NAV (a 0.4x P/NAV) implies prices below the deck
    m2, _ = npv.implied_price_multiplier(inp, x, base, 1.0, 0.0, market_cap=0.4 * nav1)
    assert m2 < 1.0
    # and a fair multiple of 0.4 brings the implied multiplier back to 1.0
    m3, _ = npv.implied_price_multiplier(inp, x, base, 1.0, 0.0, market_cap=0.4 * nav1, fair_multiple=0.4)
    assert m3 == pytest.approx(1.0, abs=1e-6)


def test_equivalents_and_missing_recovery_flag():
    deck = decks.Deck("t", {"au": 2000.0, "ag": 25.0, "cu": 4.0})
    eq, notes = equiv.equivalent_units({"au": 1_000_000, "ag": 40_000_000, "cu": 500_000_000}, deck, "au",
                                       {"au": 0.9, "ag": 0.8})
    # 0.9*1e6*2000 + 0.8*40e6*25 + 1.0*5e8*4  all / 2000
    assert eq == pytest.approx((0.9e6 * 2000 + 0.8 * 40e6 * 25 + 5e8 * 4) / 2000)
    assert any("CU" in n for n in notes) and not any("AG" in n for n in notes)
