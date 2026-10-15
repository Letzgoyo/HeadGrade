"""Project checklist: 24 yes/no tests of how ready a project is, shown as a 5x5 grid beside (never inside) the grades.

Every square is one of: yes, no, unknown (we found no sourced fact), na (does not apply to this stage). Unknown is never
turned into a guess, and a square is only yes/no when a fact (with its quote) or a published calculation backs it.
The wording and thresholds are ours; thresholds are settings, not truths."""
from __future__ import annotations

from .facts import FactBook
from .grading import project_jurisdictions
from .jurisdictions import resolve_key

THRESHOLDS = {"grade_multiple": 4.0, "mine_life_years": 10.0, "npv_capex": 2.0, "measured_share": 0.30, "jurisdiction_score": 60.0,
              "runway_months": 12.0, "max_dilution_pct": 50.0, "net_debt_ebitda": 1.0}
STUDY_RANK = {"none": 0, "PEA": 1, "PFS": 2, "FS": 3, "construction": 4, "production": 4}
GRADE_CATS = ("measured_indicated", "indicated", "measured", "inferred")

# (id, wording, how it is decided). The 13th square (index 12) is the free one.
SQUARES = [
    ("grade", "Average grade is at least 4x the cutoff grade", "numbers"),
    ("life", "Mine life is more than 10 years", "numbers"),
    ("metallurgy", "Simple, proven processing", "quote"),
    ("jurisdiction", "Jurisdiction scores 60+ on mining policy", "numbers"),
    ("fs", "Feasibility study done, NPV at least 2x initial capex", "numbers"),
    ("pea", "Economic study done, NPV at least 2x initial capex", "numbers"),
    ("management", "Management has built a mine before", "numbers"),
    ("partner", "A strategic partner is involved", "quote"),
    ("community", "Community agreements or support disclosed", "quote"),
    ("hype", "Metal is not over-hyped (HeadGrade view)", "view"),
    ("opposition", "No community opposition or legal challenge disclosed", "quote"),
    ("measured", "Measured resource is at least 30% of the total", "numbers"),
    ("free", "Free square", "free"),
    ("water", "Water supply and contamination are not an issue", "quote"),
    ("pfs", "Pre-feasibility study or better completed", "numbers"),
    ("supply", "Metal expected to be in short supply (HeadGrade view)", "view"),
    ("major_partner", "Partner is a major or well-funded operator", "quote"),
    ("pathway", "Permitting pathway looks favourable", "quote"),
    ("low_cost", "Low-cost, low-dilution mining method", "quote"),
    ("permits", "Key permits already granted", "numbers"),
    ("baseline", "Environmental baseline studies done or under way", "numbers"),
    ("infrastructure", "Power, road and water infrastructure available", "quote"),
    ("financing", "Funded, or financing likely achievable", "numbers"),
    ("working_capital", "Cash comfortably covers the next 12 months", "numbers"),
    ("expansion", "Stated upside to expand the resource or reserves", "quote"),
]


def _pick(fb: FactBook, key: str, label: str, **kw):
    """Look a fact up without noise; record it as an input (so it appears under Sources) only if it exists."""
    if fb.get(key, record=False, **kw) is None:
        return None
    return fb.get(key, label=label, **kw)


def _ev(*facts):
    return [{"fact_id": f.id, "doc_id": f.doc_id, "page": f.page, "quote": f.quote, "status": f.status, "verified_by": f.verified_by}
            for f in facts if f is not None]


def _sq(sid, state, detail="", facts=()):
    title, how = next((t, h) for i, t, h in SQUARES if i == sid)
    ev = _ev(*facts)
    return {"id": sid, "title": title, "how": how, "state": state, "detail": detail, "evidence": ev,
            "unverified": any(e["status"] not in ("verified", "override") for e in ev)}


def lead_project(fb: FactBook, projects):
    """The project carrying the most weight in the company (stated share of production/NAV); else the first."""
    if not projects:
        return None
    best = max(projects, key=lambda p: (fb.num("project_weight_pct", project_id=p.id, record=False) or 0, -p.id))
    return best


def evaluate(fb: FactBook, stage: str, projects, val: dict, jurisdiction_table: dict[str, float], primary: str,
             thresholds: dict | None = None, views: dict | None = None) -> dict:
    th = {**THRESHOLDS, **(thresholds or {})}
    if stage == "royalty":
        return {"applicable": False, "reason": "The checklist tests a single project's readiness; royalty and streaming portfolios are not scored this way.",
                "thresholds": th, "squares": [], "counts": {}}
    lead = lead_project(fb, projects)
    pid = lead.id if lead else None
    m = val.get("metrics", {})
    out: dict[str, dict] = {}
    metal = primary
    mkw = dict(project_id=pid)

    # ---- grade vs cutoff
    g = next((f for c in GRADE_CATS if (f := _pick(fb, "resource_grade", "Checklist: resource grade", metal=metal, category=c, **mkw))), None)
    cut = _pick(fb, "cutoff_grade", "Checklist: cutoff grade", metal=metal, **mkw)
    if g and cut and g.value_num and cut.value_num:
        if (g.unit or "") != (cut.unit or ""):
            out["grade"] = _sq("grade", "unknown", f"grade is in {g.unit}, cutoff in {cut.unit}: not compared", (g, cut))
        else:
            r = g.value_num / cut.value_num
            out["grade"] = _sq("grade", "yes" if r >= th["grade_multiple"] else "no", f"grade {g.value_num:g} vs cutoff {cut.value_num:g} {g.unit or ''} = {r:.1f}x", (g, cut))
    else:
        out["grade"] = _sq("grade", "unknown", "grade or cutoff grade not found")

    # ---- life
    life = _pick(fb, "mine_life_years", "Checklist: mine life", **mkw) or _pick(fb, "reserve_life_years", "Checklist: reserve life")
    out["life"] = (_sq("life", "yes" if life.value_num > th["mine_life_years"] else "no", f"{life.value_num:g} years", (life,))
                   if life and life.value_num is not None else _sq("life", "unknown", "mine or reserve life not found"))

    # ---- stage and study economics
    stg = _pick(fb, "study_stage", "Checklist: study stage", **mkw)
    rank = STUDY_RANK.get(stg.value_text, None) if stg and stg.value_text else None
    if rank is None and stage == "explorer":
        rank = 0
    producing = stage == "producer"
    npv, capex = _pick(fb, "npv_after_tax", "Checklist: after-tax NPV", **mkw), _pick(fb, "initial_capex", "Checklist: initial capex", **mkw)
    ratio = None
    if npv and capex and npv.value_num is not None and capex.value_num and (npv.currency == capex.currency or not (npv.currency and capex.currency)):
        ratio = npv.value_num / capex.value_num
    why_stage = f"company is a {stage}" if producing else (f"study stage: {stg.value_text}" if stg else "no economic study found" if stage == "explorer" else "study stage not found")
    for sid, need in (("pea", 1), ("pfs", 2), ("fs", 3)):
        if producing:
            out[sid] = _sq(sid, "na", "already in production")
        elif rank is None:
            out[sid] = _sq(sid, "unknown", why_stage)
        elif sid == "pfs":
            out[sid] = _sq(sid, "yes" if rank >= need else "no", why_stage, (stg,))
        elif rank < need:
            out[sid] = _sq(sid, "no", why_stage, (stg,))
        elif ratio is None:
            out[sid] = _sq(sid, "unknown", f"{why_stage}; NPV or capex not found", (stg, npv, capex))
        else:
            out[sid] = _sq(sid, "yes" if ratio >= th["npv_capex"] else "no", f"NPV/capex = {ratio:.1f}x ({why_stage})", (stg, npv, capex))

    # ---- jurisdiction (Fraser policy score, weighted by project share)
    rows = project_jurisdictions(FactBook(fb.facts), projects, {resolve_key(k): v for k, v in jurisdiction_table.items()})
    scored = [r for r in rows if r["score"] is not None]
    if scored:
        wt = [r["weight"] or 0 for r in scored]
        score = sum(r["score"] * w for r, w in zip(scored, wt)) / sum(wt) if sum(wt) else sum(r["score"] for r in scored) / len(scored)
        out["jurisdiction"] = _sq("jurisdiction", "yes" if score >= th["jurisdiction_score"] else "no", f"policy score {score:.0f}/100 (Fraser Institute), weighted across projects")
    else:
        out["jurisdiction"] = _sq("jurisdiction", "unknown", "jurisdiction or its policy score not found")

    # ---- management
    built = _pick(fb, "team.prior_mines_built", "Checklist: prior mines built")
    out["management"] = (_sq("management", "yes" if built.value_num >= 1 else "no", f"{built.value_num:g} mine(s) built before", (built,))
                         if built and built.value_num is not None else _sq("management", "unknown", "track record not stated in the documents read"))

    # ---- measured share of resource
    cats = {c: _pick(fb, "resource_contained", f"Checklist: {c} resource", metal=metal, category=c, **mkw) for c in ("measured", "indicated", "inferred")}
    if all(cats.values()) and all(c.value_num is not None for c in cats.values()):
        tot = sum(c.value_num for c in cats.values())
        share = cats["measured"].value_num / tot if tot else 0
        out["measured"] = _sq("measured", "yes" if share >= th["measured_share"] else "no", f"measured is {100 * share:.0f}% of the {metal.upper()} resource", tuple(cats.values()))
    else:
        out["measured"] = _sq("measured", "unknown", "needs measured, indicated and inferred figures reported separately")

    # ---- permits and baseline
    ps = _pick(fb, "permit_stage", "Checklist: permit stage", **mkw)
    if ps and ps.value_text in ("approved", "fully_permitted", "none", "baseline", "in_process"):
        out["permits"] = _sq("permits", "yes" if ps.value_text in ("approved", "fully_permitted") else "no", f"permit stage: {ps.value_text}", (ps,))
    else:
        out["permits"] = _sq("permits", "unknown", "permit stage not found")

    # ---- quote-backed statements
    def stmt(sid, key, label, invert=False):
        f = _pick(fb, key, f"Checklist: {label}", **mkw)
        if f is None or f.value_text not in ("yes", "no"):
            return _sq(sid, "unknown", "no sourced statement found")
        yes = (f.value_text == "yes") != invert
        return _sq(sid, "yes" if yes else "no", "stated in the documents (see quote)", (f,))
    for sid, key, label in (("metallurgy", "check.simple_metallurgy", "processing"), ("partner", "check.strategic_partner", "strategic partner"),
                            ("major_partner", "check.partner_is_major", "partner is a major"), ("community", "check.community_support", "community support"),
                            ("opposition", "check.no_opposition", "no opposition"), ("water", "check.water_ok", "water"),
                            ("pathway", "check.permitting_pathway", "permitting pathway"), ("low_cost", "check.low_cost_mining", "mining method"),
                            ("infrastructure", "check.infrastructure_ok", "infrastructure")):
        out[sid] = stmt(sid, key, label)
    bs = _pick(fb, "check.baseline_studies", "Checklist: baseline studies", **mkw)
    if bs and bs.value_text == "yes" or (ps and ps.value_text in ("baseline", "in_process", "approved", "fully_permitted")):
        out["baseline"] = _sq("baseline", "yes", "baseline work under way or permits advanced", (bs or ps,))
    elif bs and bs.value_text == "no" or (ps and ps.value_text == "none"):
        out["baseline"] = _sq("baseline", "no", "no baseline work reported", (bs or ps,))
    else:
        out["baseline"] = _sq("baseline", "unknown", "no sourced statement found")
    ex = _pick(fb, "expansion_potential", "Checklist: expansion upside", **mkw)
    out["expansion"] = _sq("expansion", "yes", "the company states expansion or exploration upside (see quote)", (ex,)) if ex and ex.value_text else _sq("expansion", "unknown", "no sourced statement found")

    # ---- money
    gap, dil = m.get("funding_gap_usd"), m.get("dilution_if_all_equity_pct")
    if stage != "developer":
        out["financing"] = _sq("financing", "na", "no build capex to fund" if stage == "explorer" else "already in production")
    elif gap is None:
        out["financing"] = _sq("financing", "unknown", "capex or cash not found")
    elif gap <= 0:
        out["financing"] = _sq("financing", "yes", "cash covers the initial capex")
    else:
        d = dil if dil is not None else 1e9
        out["financing"] = _sq("financing", "yes" if d <= th["max_dilution_pct"] else "no",
                               f"funding gap ${gap / 1e6:,.0f}M; equity alone would dilute about {d:.0f}% (we treat up to {th['max_dilution_pct']:.0f}% as achievable)")
    cash, burn = _pick(fb, "cash", "Checklist: cash"), _pick(fb, "quarterly_cash_burn", "Checklist: quarterly cash burn")
    if stage == "producer":
        nd = m.get("net_debt_to_ebitda")
        nd_usd = m.get("net_debt_usd")
        if nd_usd is not None and nd_usd <= 0:
            out["working_capital"] = _sq("working_capital", "yes", "net cash", (cash,))
        elif nd is not None:
            out["working_capital"] = _sq("working_capital", "yes" if nd <= th["net_debt_ebitda"] else "no", f"net debt is {nd:.1f}x EBITDA", (cash,))
        else:
            out["working_capital"] = _sq("working_capital", "unknown", "net debt or EBITDA not found")
    elif cash and burn and cash.value_num is not None and burn.value_num:
        months = cash.value_num / (burn.value_num / 3)
        out["working_capital"] = _sq("working_capital", "yes" if months >= th["runway_months"] else "no", f"about {months:.0f} months of cash at the current burn", (cash, burn))
    else:
        out["working_capital"] = _sq("working_capital", "unknown", "cash or burn rate not found")

    # ---- house views (blank until we publish one)
    v = (views or {}).get(metal)
    for sid in ("hype", "supply"):
        if v and sid in v:
            out[sid] = _sq(sid, "yes" if v[sid] else "no", f"HeadGrade view{(': ' + v['note']) if v.get('note') else ''}")
        else:
            out[sid] = _sq(sid, "unknown", f"no HeadGrade view set for {metal.upper()} yet")
    out["free"] = _sq("free", "free", "")

    squares = [out[i] for i, _, _ in SQUARES]
    counts = {s: sum(1 for q in squares if q["state"] == s) for s in ("yes", "no", "unknown", "na")}
    counts["applicable"] = counts["yes"] + counts["no"] + counts["unknown"]
    return {"applicable": True, "lead_project": lead.name if lead else None, "metal": metal, "thresholds": th, "squares": squares, "counts": counts}
