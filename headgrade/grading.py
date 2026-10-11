"""Quality grade (team, asset, jurisdiction, financial strength, growth) kept SEPARATE from the
valuation grade, so 'good company' and 'good price' are never blurred together. Scores use only
facts that exist; a category with no data is left out and lowers confidence instead of being guessed."""
from __future__ import annotations

from datetime import date

from .facts import FactBook
from .jurisdictions import resolve_key
from .valuation import scale

WEIGHTS = {"team": 20, "asset": 30, "jurisdiction": 15, "financial": 20, "growth": 15}
PERMIT_POINTS = {"none": 10, "baseline": 25, "in_process": 55, "approved": 85, "fully_permitted": 100}
LETTERS = [(80, "A"), (65, "B"), (50, "C"), (35, "D"), (0, "F")]


def letter(score: float | None) -> str:
    if score is None:
        return "n/a"
    return next(l for t, l in LETTERS if score >= t)


def _avg(parts: list[float]) -> float | None:
    return sum(parts) / len(parts) if parts else None


def _team(fb: FactBook, notes: list[str]) -> float | None:
    parts = []
    built = fb.num("team.prior_mines_built", label="Prior mines built")
    if built is not None:
        parts.append({0: 20, 1: 60, 2: 80}.get(int(built), 100)); notes.append(f"management has built {int(built)} mine(s) before")
    own = fb.num("team.insider_ownership_pct", label="Insider ownership %")
    if own is not None:
        parts.append(scale(own, 0, 15)); notes.append(f"insiders hold {own:g}% of shares")
    shares = sorted([x for x in fb.all("shares_basic") if x.as_of and x.value_num], key=lambda x: x.as_of)
    if len(shares) >= 2 and (shares[-1].as_of - shares[0].as_of).days >= 300:
        yrs = (shares[-1].as_of - shares[0].as_of).days / 365.25
        cagr = (shares[-1].value_num / shares[0].value_num) ** (1 / yrs) - 1
        parts.append(scale(cagr, 0.30, 0.0)); notes.append(f"share count grew {100 * cagr:.0f}% a year over {yrs:.1f} years")
    base = _avg(parts)
    acts = fb.num("team.regulatory_actions", label="Regulatory actions")
    if base is None:
        return None
    if acts:
        base = max(0.0, base - 25 * acts); notes.append(f"{int(acts)} disclosed regulatory action(s): score reduced")
    return base


def _asset(fb: FactBook, stage: str, val: dict, projects, notes: list[str]) -> float | None:
    m = val.get("metrics", {})
    parts: list[float] = []
    if stage == "developer":
        for p in projects:
            irr = fb.num("irr_after_tax", project_id=p.id, label=f"{p.name}: after-tax IRR")
            if irr is not None:
                parts.append(scale(irr, 10, 30)); notes.append(f"{p.name}: after-tax IRR {irr:g}% at the study's prices")
            life = fb.num("mine_life_years", project_id=p.id, label=f"{p.name}: mine life")
            if life is not None:
                parts.append(scale(life, 5, 15)); notes.append(f"{p.name}: {life:g}-year mine life")
    elif stage == "producer":
        if m.get("aisc_margin_at_deck") is not None:
            parts.append(scale(m["aisc_margin_at_deck"], 0.20, 0.50)); notes.append(f"AISC margin {100 * m['aisc_margin_at_deck']:.0f}% at the deck price")
        if m.get("reserve_life_years") is not None:
            parts.append(scale(m["reserve_life_years"], 3, 12)); notes.append(f"reserve life {m['reserve_life_years']:g} years")
    elif stage == "explorer":
        if m.get("mi_share_of_resource") is not None:
            parts.append(scale(m["mi_share_of_resource"], 0.2, 0.8)); notes.append(f"{100 * m['mi_share_of_resource']:.0f}% of the resource is measured/indicated")
    elif stage == "royalty":
        if m.get("asset_count") is not None:
            parts.append(scale(m["asset_count"], 3, 20)); notes.append(f"{int(m['asset_count'])} assets in the portfolio")
        if m.get("top_asset_share_pct") is not None:
            parts.append(scale(m["top_asset_share_pct"], 70, 25)); notes.append(f"largest asset is {m['top_asset_share_pct']:g}% of the portfolio")
    return _avg(parts)


def project_jurisdictions(fb: FactBook, projects, table: dict[str, float]) -> list[dict]:
    """Per project: its jurisdiction, that jurisdiction's policy score, and its stated weight in the company (if any)."""
    out = []
    for p in projects:
        name = fb.text("jurisdiction", project_id=p.id, label=f"{p.name}: jurisdiction", record=True) or None
        w = fb.num("project_weight_pct", project_id=p.id, label=f"{p.name}: share of production/NAV")
        score = table.get(resolve_key(name)) if name else None
        out.append({"project": p.name, "jurisdiction": name, "score": score, "weight": w,
                    "permit": fb.text("permit_stage", project_id=p.id, label=f"{p.name}: permit stage")})
    return out


def _jurisdiction(fb: FactBook, projects, table: dict[str, float], notes: list[str]) -> float | None:
    rows = project_jurisdictions(fb, projects, table)
    scored = [r for r in rows if r["score"] is not None]
    for r in rows:
        if r["jurisdiction"] and r["score"] is None:
            notes.append(f"{r['project']}: '{r['jurisdiction']}' is not in the jurisdiction table")
        if r["permit"] in PERMIT_POINTS:
            notes.append(f"{r['project']}: permitting is '{r['permit']}'")
    j = None
    if scored:
        if all(r["weight"] for r in scored):
            tot = sum(r["weight"] for r in scored)
            j = sum(r["score"] * r["weight"] for r in scored) / tot
            notes.append("weighted by each project's share of production/NAV: " + "; ".join(
                f"{r['project']} ({r['jurisdiction']}) {r['weight']:g}% at {r['score']:.0f}/100" for r in scored))
        else:
            j = sum(r["score"] for r in scored) / len(scored)
            notes.append("projects weighted equally (production shares not found): " + "; ".join(
                f"{r['project']} ({r['jurisdiction']}) {r['score']:.0f}/100" for r in scored))
    ps = [PERMIT_POINTS[r["permit"]] for r in rows if r["permit"] in PERMIT_POINTS]
    pm = _avg(ps)
    if j is None and pm is None:
        return None
    if j is None:
        return pm
    if pm is None:
        return j
    return 0.6 * j + 0.4 * pm


def _financial(stage: str, val: dict, notes: list[str]) -> float | None:
    m, parts = val.get("metrics", {}), []
    if stage == "explorer" and m.get("runway_months") is not None:
        parts.append(scale(m["runway_months"], 3, 24)); notes.append(f"cash runway about {m['runway_months']:.0f} months")
    if stage == "developer":
        if m.get("funding_gap_vs_market_cap") is not None:
            parts.append(scale(m["funding_gap_vs_market_cap"], 1.0, 0.0))
            notes.append(f"funding gap is {100 * m['funding_gap_vs_market_cap']:.0f}% of market cap")
    if stage == "producer":
        if m.get("net_debt_to_ebitda") is not None:
            parts.append(scale(m["net_debt_to_ebitda"], 3.0, 0.0)); notes.append(f"net debt is {m['net_debt_to_ebitda']:.1f}x EBITDA")
        if m.get("fcf_at_stress_usd") is not None and m.get("market_cap_usd"):
            parts.append(scale(m["fcf_at_stress_usd"] / m["market_cap_usd"], -0.05, 0.05))
            notes.append(f"free cash flow {'stays positive' if m['fcf_at_stress_usd'] > 0 else 'turns negative'} if metals fall 20%")
    if stage == "royalty" and m.get("ev_usd") is not None and m.get("market_cap_usd"):
        parts.append(scale(m["market_cap_usd"] / m["ev_usd"], 0.8, 1.2))  # EV below market cap means net cash
        notes.append("balance sheet: net cash" if m["ev_usd"] < m["market_cap_usd"] else "balance sheet: net debt")
    return _avg(parts)


def _red_flags(fb: FactBook, stage: str, val: dict, today: date, projects=(), table: dict | None = None) -> list[dict]:
    m, out = val.get("metrics", {}), []
    add = lambda sev, text: out.append({"severity": sev, "text": text})
    if m.get("dilution_if_all_equity_pct") and m["dilution_if_all_equity_pct"] > 50:
        add("high", f"Funding the project's capex entirely with equity would dilute shareholders by about {m['dilution_if_all_equity_pct']:.0f}%.")
    if m.get("runway_months") is not None and m["runway_months"] < 6:
        add("high", f"Cash runway is about {m['runway_months']:.0f} months at the current burn.")
    if (fb.num("team.regulatory_actions", record=False) or 0) > 0:
        add("high", "Regulatory or securities actions are disclosed against the company or its officers.")
    if m.get("net_debt_to_ebitda") is not None and m["net_debt_to_ebitda"] > 3:
        add("medium", f"Net debt is {m['net_debt_to_ebitda']:.1f}x EBITDA.")
    sd = fb.get("study_date", record=False)
    if sd is not None and sd.as_of is None and sd.value_text:
        try:
            d = date.fromisoformat(sd.value_text[:10])
            if (today - d).days > 3 * 365:
                add("medium", f"The economic study is {((today - d).days / 365):.0f} years old; its costs may be stale.")
        except ValueError:
            pass
    for r in project_jurisdictions(FactBook(fb.facts), projects, table or {}):
        if r["score"] is not None and r["score"] < 40 and (r["weight"] or 0) >= 20:
            add("high", f"{r['weight']:g}% of production/NAV is in {r['jurisdiction']}, which scores {r['score']:.0f}/100 on mining policy (Fraser Institute).")
    for row in val.get("tables", {}).get("projects", []):
        if isinstance(row.get("study_prices"), dict) and isinstance(row.get("deck_prices"), dict):
            ratio = max(row["deck_prices"][k] / row["study_prices"][k] for k in row["study_prices"] if row["study_prices"][k])
            if ratio > 1.5:
                add("medium", f"{row['project']}: the deck prices are up to {ratio:.1f}x the study's prices, so NAV leans heavily on the price gap rather than the project.")
    return out


def grade(fb: FactBook, stage: str, projects, valuation: dict, jurisdiction_table: dict[str, float] | None = None,
          today: date | None = None) -> dict:
    today = today or date.today()
    table = {resolve_key(k): v for k, v in (jurisdiction_table or {}).items()}
    cats: dict[str, dict] = {}
    scorers = {
        "team": lambda n: _team(fb, n),
        "asset": lambda n: _asset(fb, stage, valuation, projects, n),
        "jurisdiction": lambda n: _jurisdiction(fb, projects, table, n),
        "financial": lambda n: _financial(stage, valuation, n),
        "growth": lambda n: (n.append("growth is not scored yet: it needs resource history over time") or None),
    }
    for name, fn in scorers.items():
        notes: list[str] = []
        sc = fn(notes)
        cats[name] = {"score": sc, "grade": letter(sc), "weight": WEIGHTS[name], "notes": notes}
    scored = {k: v for k, v in cats.items() if v["score"] is not None}
    wsum = sum(v["weight"] for v in scored.values())
    quality = sum(v["score"] * v["weight"] for v in scored.values()) / wsum if len(scored) >= 3 else None
    value = valuation.get("value_score")
    if quality is None:
        quadrant = "Not enough data to place"
    elif value is None:
        quadrant = "Quality scored; valuation not rated"
    elif quality >= 65 and value >= 60:
        quadrant = "Higher quality, valued at a discount to its stage"
    elif quality >= 65:
        quadrant = "Higher quality, valued at or above its stage"
    elif value >= 60:
        quadrant = "Lower quality, but cheap on its stage: check why"
    else:
        quadrant = "Lower quality and not cheap on its stage"
    conf = fb.confidence()
    coverage = len(scored) / len(WEIGHTS)
    level = ("high" if coverage >= 0.8 and conf["verified_share"] >= 0.8 else
             "medium" if coverage >= 0.6 and conf["verified_share"] >= 0.5 else "low")
    return {
        "quality_score": quality, "quality_grade": letter(quality), "value_score": value, "value_grade": letter(value),
        "quadrant": quadrant, "categories": cats, "red_flags": _red_flags(fb, stage, valuation, today, projects, table),
        "confidence": {"level": level, "category_coverage": round(coverage, 2), **conf},
        "missing": sorted(set(fb.missing)),
    }
