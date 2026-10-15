from datetime import date

from headgrade import checklist, valuation as V
from headgrade.facts import F, FactBook

P = V.Proj(1, "Alpha", "au")


def fb(*facts, verified_only=False):
    rows = [F(k, n, t, u, c, m, cat, 1 if k in PROJ else None, date(2026, 1, 1), st, 0.9, i + 1, 1, 3, f"quote for {k}", i + 1, "auto")
            for i, (k, n, t, u, c, m, cat, st) in enumerate(facts)]
    return FactBook(rows, include_unverified=not verified_only)


PROJ = {"resource_grade", "cutoff_grade", "mine_life_years", "study_stage", "npv_after_tax", "initial_capex", "permit_stage", "resource_contained",
        "project_weight_pct", "jurisdiction", "expansion_potential"}


def f(key, num=None, text=None, unit=None, ccy=None, metal=None, cat=None, status="verified"):
    if key.startswith("check."):
        PROJ.add(key)
    return (key, num, text, unit, ccy, metal, cat, status)


def run(facts, stage="developer", val=None, table=None, **kw):
    return checklist.evaluate(fb(*facts), stage, [P], val or {"metrics": {}}, table or {}, "au", **kw)


def by(cl):
    return {q["id"]: q for q in cl["squares"]}


def test_grid_is_5x5_with_free_centre_and_nothing_known_means_unknown():
    cl = run([])
    assert len(cl["squares"]) == 25 and cl["squares"][12]["state"] == "free"
    s = by(cl)
    assert cl["counts"]["yes"] == 0 and cl["counts"]["no"] == 0
    assert s["grade"]["state"] == "unknown" and s["partner"]["state"] == "unknown" and s["hype"]["state"] == "unknown"
    assert "no HeadGrade view" in s["hype"]["detail"]
    assert s["financing"]["state"] == "unknown"           # developer with no capex facts


def test_numeric_squares_and_thresholds():
    cl = run([f("resource_grade", 1.2, unit="g/t", metal="au", cat="indicated"), f("cutoff_grade", 0.3, unit="g/t", metal="au"),
              f("mine_life_years", 12), f("study_stage", text="FS"), f("npv_after_tax", 900e6, ccy="USD"), f("initial_capex", 400e6, ccy="USD"),
              f("permit_stage", text="approved"), f("team.prior_mines_built", 2)])
    s = by(cl)
    assert s["grade"]["state"] == "yes" and "4.0x" in s["grade"]["detail"]
    assert s["life"]["state"] == "yes"
    assert (s["fs"]["state"], s["pea"]["state"], s["pfs"]["state"]) == ("yes", "yes", "yes") and "2.2x" in s["fs"]["detail"]
    assert s["permits"]["state"] == "yes" and s["baseline"]["state"] == "yes" and s["management"]["state"] == "yes"
    # a stricter setting flips the same facts
    cl2 = run([f("resource_grade", 1.2, unit="g/t", metal="au", cat="indicated"), f("cutoff_grade", 0.3, unit="g/t", metal="au")], thresholds={"grade_multiple": 5})
    assert by(cl2)["grade"]["state"] == "no"


def test_grade_units_must_match_and_pea_only_is_not_feasibility():
    cl = run([f("resource_grade", 1.2, unit="g/t", metal="au", cat="indicated"), f("cutoff_grade", 0.3, unit="%", metal="au"),
              f("study_stage", text="PEA"), f("npv_after_tax", 900e6, ccy="USD"), f("initial_capex", 300e6, ccy="USD")])
    s = by(cl)
    assert s["grade"]["state"] == "unknown"
    assert (s["pea"]["state"], s["pfs"]["state"], s["fs"]["state"]) == ("yes", "no", "no")


def test_measured_share_needs_all_three_categories():
    base = [f("resource_contained", 3e5, unit="oz", metal="au", cat="measured"), f("resource_contained", 5e5, unit="oz", metal="au", cat="indicated")]
    assert by(run(base))["measured"]["state"] == "unknown"
    s = by(run(base + [f("resource_contained", 2e5, unit="oz", metal="au", cat="inferred")]))
    assert s["measured"]["state"] == "yes" and "30%" in s["measured"]["detail"]


def test_quote_statements_yes_no_and_silence_is_not_no():
    cl = run([f("check.simple_metallurgy", text="yes"), f("check.water_ok", text="no")])
    s = by(cl)
    assert s["metallurgy"]["state"] == "yes" and s["metallurgy"]["evidence"][0]["quote"] == "quote for check.simple_metallurgy"
    assert s["water"]["state"] == "no" and s["community"]["state"] == "unknown"


def test_stage_applicability_and_money():
    prod = run([], stage="producer", val={"metrics": {"net_debt_usd": -5e8, "net_debt_to_ebitda": -0.5}})
    s = by(prod)
    assert (s["pea"]["state"], s["fs"]["state"], s["financing"]["state"]) == ("na", "na", "na") and s["working_capital"]["state"] == "yes"
    assert prod["counts"]["applicable"] == 25 - 1 - prod["counts"]["na"]
    dev = by(run([], val={"metrics": {"funding_gap_usd": 3e8, "dilution_if_all_equity_pct": 80}}))
    assert dev["financing"]["state"] == "no" and "80%" in dev["financing"]["detail"]
    dev2 = by(run([], val={"metrics": {"funding_gap_usd": 3e8, "dilution_if_all_equity_pct": 30}}))
    assert dev2["financing"]["state"] == "yes"
    exp = by(run([f("cash", 12e6, ccy="USD"), f("quarterly_cash_burn", 2e6, ccy="USD")], stage="explorer"))
    assert exp["working_capital"]["state"] == "yes" and exp["pea"]["state"] == "no" and exp["financing"]["state"] == "na"


def test_royalty_is_not_applicable_and_views_fill_the_view_squares():
    assert run([], stage="royalty")["applicable"] is False
    s = by(run([], views={"au": {"hype": True, "supply": False, "note": "example"}}))
    assert (s["hype"]["state"], s["supply"]["state"]) == ("yes", "no") and "HeadGrade view" in s["hype"]["detail"]


def test_unverified_evidence_is_flagged_and_lead_project_is_the_heaviest():
    s = by(run([f("check.water_ok", text="yes", status="unverified")]))
    assert s["water"]["unverified"] is True
    p2 = V.Proj(2, "Beta", "au")
    book = FactBook([F("project_weight_pct", 70, project_id=2, status="verified"), F("project_weight_pct", 30, project_id=1, status="verified")])
    assert checklist.lead_project(book, [P, p2]).name == "Beta"
