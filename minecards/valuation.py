"""Stage-specific valuation. Each method returns metrics, tables, a 0-100 value score (or None when
it cannot be rated honestly), the assumptions it leaned on, and the inputs it used or lacked."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

from . import assumptions as A, equiv, npv
from .decks import Deck
from .facts import FactBook
from .registry import METALS, METAL_UNIT


class Proj(NamedTuple):
    id: int
    name: str
    metal: str | None = None


@dataclass
class Market:
    price: float                    # per share, in the trading currency
    currency: str = "USD"           # trading currency
    price_date: date | None = None
    fx_usd: dict = field(default_factory=lambda: {"USD": 1.0})   # USD per 1 unit of currency


def interp(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear through (x, score) anchors sorted by x; clamped at the ends."""
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def scale(x: float, bad: float, good: float) -> float:
    """0 at `bad`, 100 at `good`, linear between, clamped. Works for either direction."""
    return max(0.0, min(100.0, 100 * (x - bad) / (good - bad)))


class _Ctx:
    def __init__(self, fb: FactBook, mk: Market, deck: Deck):
        self.fb, self.mk, self.deck = fb, mk, deck
        self.flags: list[str] = []
        self.assumptions: list[str] = []

    def usd(self, key, project_id=None, label=None, metal=None, default_ccy=None):
        """A monetary fact converted to USD, or None (and a flag) if it or its FX rate is missing."""
        f = self.fb.get(key, project_id=project_id, metal=metal, label=label)
        if f is None or f.value_num is None:
            return None
        ccy = f.currency or default_ccy or self.mk.currency
        if f.currency is None:
            self.flags.append(f"{label or key}: currency not stated, assumed {ccy}")
        rate = self.mk.fx_usd.get(ccy)
        if rate is None:
            self.flags.append(f"{label or key}: no FX rate for {ccy}, input ignored")
            return None
        return f.value_num * rate

    def price_usd(self) -> float | None:
        r = self.mk.fx_usd.get(self.mk.currency)
        if r is None:
            self.flags.append(f"no FX rate for {self.mk.currency}: cannot convert the share price")
            return None
        return self.mk.price * r

    def shares(self) -> float | None:
        fd = self.fb.num("shares_fully_diluted", label="Fully diluted shares")
        if fd:
            return fd
        basic = self.fb.num("shares_basic", label="Basic shares")
        if basic:
            self.flags.append("fully diluted share count not available: basic shares used (understates dilution)")
        return basic

    def market_cap_usd(self):
        s, p = self.shares(), self.price_usd()
        return (s * p) if s and p else None


def _result(ctx: _Ctx, stage: str, method: str, **kw) -> dict:
    out = {"stage": stage, "method": method, "value_score": None, "metrics": {}, "tables": {}, "flags": ctx.flags,
           "assumptions": ctx.assumptions, "confidence": ctx.fb.confidence(),
           "inputs_used": [u.__dict__ for u in ctx.fb.used], "missing": sorted(set(ctx.fb.missing))}
    out.update(kw)
    return out


def _net_cash(ctx: _Ctx):
    cash = ctx.usd("cash", label="Cash")
    debt = ctx.usd("debt", label="Debt")
    if debt is None:
        ctx.flags.append("debt not found: assumed zero")
        debt = 0.0
    return cash, debt


# ============================================================ DEVELOPER
def value_developer(fb: FactBook, mk: Market, projects: list[Proj], deck: Deck) -> dict:
    ctx = _Ctx(fb, mk, deck)
    ctx.assumptions += [A.TEXT["DEVELOPER_PNAV"], A.TEXT["NPV_MODEL"], A.TEXT["FUNDING"], A.TEXT["SCORING_BASIS"]]
    mcap, p_usd, shares = ctx.market_cap_usd(), ctx.price_usd(), ctx.shares()
    cash, debt = _net_cash(ctx)
    if mcap is None or cash is None:
        ctx.flags.append("market cap or cash missing: cannot value")
        return _result(ctx, "developer", "P/NAV on the company's own study, re-priced")
    net_cash = cash - debt
    rows, models = [], []
    stages = []
    for p in projects:
        pid = p.id
        metals = [m for m in METALS if fb.get("annual_production", project_id=pid, metal=m, record=False)]
        row = {"project": p.name, "status": "ok"}
        needed = {}
        npv_pub = ctx.usd("npv_after_tax", pid, f"{p.name}: after-tax NPV")
        capex = ctx.usd("initial_capex", pid, f"{p.name}: initial capex")
        life = fb.num("mine_life_years", project_id=pid, label=f"{p.name}: mine life")
        rate_pct = fb.num("npv_discount_rate", project_id=pid, label=f"{p.name}: NPV discount rate")
        own_pct = fb.num("ownership_pct", project_id=pid, label=f"{p.name}: ownership")
        stage_txt = fb.text("study_stage", project_id=pid, label=f"{p.name}: study stage")
        prod = {m: fb.num("annual_production", project_id=pid, metal=m, label=f"{p.name}: annual {m.upper()}") for m in metals}
        study_px = {m: fb.num("study_price", project_id=pid, metal=m, label=f"{p.name}: study {m.upper()} price") for m in metals}
        if None in (npv_pub, capex, life, rate_pct) or not metals or any(v is None for v in study_px.values()):
            row["status"] = "missing inputs"
            rows.append(row)
            continue
        if any(m not in deck.prices for m in metals):
            row["status"] = "deck lacks a price for " + ", ".join(m.upper() for m in metals if m not in deck.prices)
            rows.append(row)
            continue
        own = (own_pct if own_pct is not None else 100.0) / 100
        if own_pct is None:
            ctx.flags.append(f"{p.name}: ownership not stated, assumed 100%")
        C = fb.num("construction_years", project_id=pid, label=f"{p.name}: construction years")
        if C is None:
            C = A.DEFAULT_CONSTRUCTION_YEARS
            ctx.flags.append(f"{p.name}: construction period not stated, assumed {C} years")
        tax = fb.num("tax_rate", label="Tax rate")
        if tax is None:
            tax = A.DEFAULT_TAX_RATE * 100
            ctx.flags.append(f"tax rate not stated, assumed {int(tax)}%")
        roy = fb.num("royalty_burden_pct", project_id=pid, label=f"{p.name}: royalty burden") or 0.0
        inp = npv.ProjectInputs(prod, life, int(round(C)), capex, tax / 100, roy / 100, rate_pct / 100)
        opex, why = npv.calibrate_opex(inp, study_px, npv_pub)
        if opex is None:
            row["status"] = "could not reconcile: " + why
            ctx.flags.append(f"{p.name}: {why}")
            rows.append(row)
            continue
        dk = {m: deck.price(m) for m in metals}
        row.update(ownership_pct=round(own * 100, 1), published_npv_usd=npv_pub,
                   model_opex_per_year_usd=opex, npv_at_deck=npv.npv(inp, dk, opex),
                   npv_at_deck_8pct=npv.npv(inp, dk, opex, 0.08), npv_at_deck_10pct=npv.npv(inp, dk, opex, 0.10),
                   npv_at_80pct_deck=npv.npv(inp, {k: v * 0.8 for k, v in dk.items()}, opex),
                   study_stage=stage_txt, study_prices=study_px, deck_prices=dk, initial_capex_usd=capex)
        rows.append(row)
        models.append((p, inp, opex, own, dk))
        stages.append(stage_txt)
    if not models:
        ctx.flags.append("no project could be modelled")
        return _result(ctx, "developer", "P/NAV on the company's own study, re-priced", tables={"projects": rows})

    def nav_total(m=1.0, rate=None):
        return sum(own * npv.npv(inp, {k: v * m for k, v in dk.items()}, opex, rate) for _, inp, opex, own, dk in models) + net_cash

    nav = nav_total()
    pnav = mcap / nav if nav > 0 else None
    stage = next((s for s in ("PEA", "PFS", "FS", "construction") if s in stages), None)  # least advanced study governs
    rng = A.DEVELOPER_PNAV.get(stage or "", None)
    m1, why1 = npv.solve_multiplier(nav_total, mcap)
    mid = (sum(rng) / 2) if rng else None
    m_fair, why_fair = npv.solve_multiplier(lambda m: nav_total(m) * mid, mcap) if mid else (None, "")
    primary = projects[0].metal or (list(models[0][4])[0])
    metrics = {
        "market_cap_usd": mcap, "net_cash_usd": net_cash, "nav_usd": nav,
        "nav_per_share_usd": nav / shares if shares else None, "price_usd": p_usd,
        "p_nav": pnav, "p_nav_at_8pct": mcap / nav_total(1.0, 0.08) if nav_total(1.0, 0.08) > 0 else None,
        "study_stage": stage, "typical_p_nav_range": rng,
        "implied_metal_multiple_at_1x_nav": m1, "implied_metal_multiple_note": why1,
        "implied_primary_price_at_1x_nav": m1 * deck.price(primary) if primary in deck.prices else None,
        "primary_metal": primary,
        "implied_metal_multiple_at_fair_midpoint": m_fair,
        "implied_primary_price_at_fair_midpoint": (m_fair * deck.price(primary)) if m_fair and primary in deck.prices else None,
    }
    capex_att = sum(own * inp.initial_capex for _, inp, _, own, _ in models)
    gap = max(0.0, capex_att - max(cash, 0.0))
    new_sh = gap / (p_usd * (1 - A.ISSUE_DISCOUNT)) if p_usd else None
    pf_nav_ps = (nav + gap) / (shares + (new_sh or 0)) if shares else None   # raising `gap` adds that much cash; capex is already inside NPV
    metrics.update(attributable_capex_usd=capex_att, funding_gap_usd=gap,
                   dilution_if_all_equity_pct=(100 * new_sh / shares) if new_sh and shares else None,
                   funding_gap_vs_market_cap=gap / mcap if mcap else None,
                   pro_forma_nav_per_share_usd=pf_nav_ps,
                   pro_forma_p_nav=(p_usd / pf_nav_ps) if pf_nav_ps and pf_nav_ps > 0 and p_usd else None)
    sens = []
    for pm in A.SENSITIVITY_PRICE:
        row = {"price_vs_deck": pm, "nav_published_rate": nav_total(pm)}
        for r in A.SENSITIVITY_RATES:
            n = nav_total(pm, r)
            row[f"nav_at_{int(r * 100)}pct"] = n
            row[f"p_nav_at_{int(r * 100)}pct"] = (mcap / n) if n > 0 else None
        row["p_nav_published_rate"] = (mcap / row["nav_published_rate"]) if row["nav_published_rate"] > 0 else None
        sens.append(row)
    score = None
    basis = metrics["pro_forma_p_nav"] if gap > 0 and metrics.get("pro_forma_p_nav") is not None else pnav
    metrics["p_nav_used_for_score"] = basis
    if basis is not None and rng:
        low, high = rng
        score = interp(basis, [(0.5 * low, 100), (low, 70), (high, 40), (1.5 * high, 0)])
    elif rng is None:
        ctx.flags.append("no recognised study stage (PEA/PFS/FS/construction): developer value not rated")
    return _result(ctx, "developer", "P/NAV on the company's own study, re-priced at the current deck",
                   value_score=score, metrics=metrics, tables={"projects": rows, "sensitivity": sens})


# ============================================================ EXPLORER
def value_explorer(fb: FactBook, mk: Market, projects: list[Proj], deck: Deck, benchmarks: list | None,
                   primary: str = "au") -> dict:
    ctx = _Ctx(fb, mk, deck)
    ctx.assumptions.append(A.TEXT["EXPLORER"])
    mcap = ctx.market_cap_usd()
    cash, debt = _net_cash(ctx)
    burn = ctx.usd("quarterly_cash_burn", label="Quarterly cash burn")
    if mcap is None or cash is None:
        ctx.flags.append("market cap or cash missing: cannot value")
        return _result(ctx, "explorer", "EV per equivalent resource unit vs peers")
    ev = mcap + debt - cash
    mi, inf = {}, {}
    for p in projects:
        for m in METALS:
            f_mi = fb.get("resource_contained", project_id=p.id, metal=m, category="measured_indicated", record=False)
            if f_mi is None:  # sum measured + indicated if only those are stated
                a = fb.num("resource_contained", project_id=p.id, metal=m, category="measured", record=False) or 0
                b = fb.num("resource_contained", project_id=p.id, metal=m, category="indicated", record=False) or 0
                v = a + b if (a or b) else None
            else:
                v = f_mi.value_num
            i = fb.num("resource_contained", project_id=p.id, metal=m, category="inferred", record=False)
            if v:
                mi[m] = mi.get(m, 0) + v
                fb.get("resource_contained", project_id=p.id, metal=m, category="measured_indicated" if f_mi else "indicated",
                       label=f"{p.name}: {m.upper()} M&I")
            if i:
                inf[m] = inf.get(m, 0) + i
                fb.get("resource_contained", project_id=p.id, metal=m, category="inferred", label=f"{p.name}: {m.upper()} inferred")
    if not (mi or inf):
        ctx.flags.append("no resource estimate found")
        return _result(ctx, "explorer", "EV per equivalent resource unit vs peers", metrics={"ev_usd": ev})
    recs = {m: fb.num("recovery", metal=m, record=False) for m in set(mi) | set(inf)}
    recs = {m: r / 100 for m, r in recs.items() if r}
    missing = [m for m in (set(mi) | set(inf)) if m not in deck.prices]
    if missing:
        ctx.flags.append("deck lacks prices for " + ", ".join(m.upper() for m in missing))
        return _result(ctx, "explorer", "EV per equivalent resource unit vs peers")
    mi_eq, n1 = equiv.equivalent_units(mi, deck, primary, recs)
    inf_eq, n2 = equiv.equivalent_units(inf, deck, primary, recs)
    ctx.flags += sorted(set(n1 + n2))
    weighted = mi_eq + A.INFERRED_WEIGHT * inf_eq
    metrics = {
        "market_cap_usd": mcap, "ev_usd": ev, "cash_usd": cash, "primary_metal": primary,
        "mi_equivalent_units": mi_eq, "inferred_equivalent_units": inf_eq, "weighted_equivalent_units": weighted,
        "mi_share_of_resource": mi_eq / (mi_eq + inf_eq) if (mi_eq + inf_eq) else None,
        "ev_per_mi_unit_usd": ev / mi_eq if mi_eq else None,
        "ev_per_weighted_unit_usd": ev / weighted if weighted else None,
        "ev_to_cash": ev / cash if cash and cash > 0 else None,
        "runway_months": (cash / (burn / 3)) if burn else None,
        "metal_price_basis_usd": deck.price(primary),
    }
    score = None
    bench = [b for b in (benchmarks or []) if b.metal == primary and b.kind == "peer_ev_per_unit" and b.stage == "explorer"]
    x = metrics["ev_per_weighted_unit_usd"]
    if bench and x:
        b = bench[0]
        score = interp(x, [(0.0, 100), (b.p25, 80), (b.median, 55), (b.p75, 30), (2 * b.p75, 0)])
        metrics["peer_benchmark"] = {"p25": b.p25, "median": b.median, "p75": b.p75, "n": b.n, "as_of": str(b.as_of), "source": b.source}
    else:
        ctx.flags.append("no peer benchmarks loaded for this metal and stage: explorer valuation is NOT RATED (metrics shown for reference)")
    return _result(ctx, "explorer", "EV per equivalent resource unit vs peers", value_score=score, metrics=metrics)


# ============================================================ PRODUCER
def value_producer(fb: FactBook, mk: Market, projects: list[Proj], deck: Deck, primary: str = "au") -> dict:
    ctx = _Ctx(fb, mk, deck)
    ctx.assumptions += [A.TEXT["PRODUCER"], f"Stress test: metal prices {int(A.PRICE_STRESS * 100)}% below the deck."]
    mcap = ctx.market_cap_usd()
    cash, debt = _net_cash(ctx)
    if mcap is None or cash is None:
        ctx.flags.append("market cap or cash missing: cannot value")
        return _result(ctx, "producer", "Normalised free cash flow yield and EV/EBITDA")
    ev = mcap + debt - cash
    ocf = ctx.usd("op_cash_flow_ttm", label="Operating cash flow (TTM)")
    capex = ctx.usd("capex_ttm", label="Capex (TTM)")
    ebitda = ctx.usd("ebitda_ttm", label="EBITDA (TTM)")
    rev = ctx.usd("revenue_ttm", label="Revenue (TTM)")
    tax = (fb.num("tax_rate", label="Tax rate") or A.DEFAULT_TAX_RATE * 100) / 100
    fcf = (ocf - capex) if ocf is not None and capex is not None else None
    prod = {m: fb.num("production_ttm", metal=m, label=f"{m.upper()} production (TTM)") for m in METALS
            if fb.get("production_ttm", metal=m, record=False)}
    real = {m: fb.num("realized_price", metal=m, label=f"{m.upper()} realized price") for m in prod
            if fb.get("realized_price", metal=m, record=False)}
    norm = None
    if fcf is not None and prod and all(m in real and m in deck.prices for m in prod):
        adj = sum(prod[m] * (real[m] - deck.price(m)) for m in prod) * (1 - tax)
        norm = fcf - adj
    elif fcf is not None:
        ctx.flags.append("realized prices or production missing: FCF not normalised to the deck")
    stress = None
    if norm is not None or fcf is not None:
        base = norm if norm is not None else fcf
        if prod and all(m in deck.prices for m in prod):
            stress = base - A.PRICE_STRESS * sum(prod[m] * deck.price(m) for m in prod) * (1 - tax)
    aisc = fb.num("aisc_per_unit", metal=primary, label=f"{primary.upper()} AISC")
    margin = (deck.price(primary) - aisc) / deck.price(primary) if aisc is not None and primary in deck.prices else None
    metrics = {
        "market_cap_usd": mcap, "ev_usd": ev, "net_debt_usd": debt - cash, "fcf_ttm_usd": fcf,
        "fcf_yield_reported": fcf / mcap if fcf is not None else None,
        "fcf_normalised_usd": norm, "fcf_yield_normalised": norm / mcap if norm is not None else None,
        "fcf_at_stress_usd": stress, "fcf_yield_at_stress": stress / mcap if stress is not None else None,
        "ev_to_ebitda": ev / ebitda if ebitda and ebitda > 0 else None,
        "net_debt_to_ebitda": (debt - cash) / ebitda if ebitda and ebitda > 0 else None,
        "aisc_margin_at_deck": margin, "reserve_life_years": fb.num("reserve_life_years", label="Reserve life"),
        "revenue_ttm_usd": rev, "primary_metal": primary,
    }
    parts = []
    y = metrics["fcf_yield_normalised"] if metrics["fcf_yield_normalised"] is not None else metrics["fcf_yield_reported"]
    if y is not None:
        parts.append(scale(y, *A.PRODUCER_FCF_YIELD))
    if metrics["ev_to_ebitda"] is not None:
        parts.append(scale(metrics["ev_to_ebitda"], *A.PRODUCER_EV_EBITDA))
    score = sum(parts) / len(parts) if parts else None
    if score is None:
        ctx.flags.append("not enough producer data to rate value")
    return _result(ctx, "producer", "Normalised free cash flow yield and EV/EBITDA", value_score=score, metrics=metrics)


# ============================================================ ROYALTY
def value_royalty(fb: FactBook, mk: Market, deck: Deck, benchmarks: list | None, primary: str = "au") -> dict:
    ctx = _Ctx(fb, mk, deck)
    ctx.assumptions.append("Royalty/stream companies are valued against peer price-to-cash-flow only; without loaded benchmarks they are 'not rated'.")
    mcap = ctx.market_cap_usd()
    cash, debt = _net_cash(ctx)
    if mcap is None or cash is None:
        ctx.flags.append("market cap or cash missing: cannot value")
        return _result(ctx, "royalty", "Price to cash flow vs peers")
    ev = mcap + debt - cash
    ocf = ctx.usd("op_cash_flow_ttm", label="Operating cash flow (TTM)")
    rev = ctx.usd("royalty_revenue_ttm", label="Royalty revenue (TTM)")
    geo = fb.num("attributable_production_ttm", metal=primary, label="Attributable production (TTM)")
    metrics = {"market_cap_usd": mcap, "ev_usd": ev, "price_to_cash_flow": mcap / ocf if ocf and ocf > 0 else None,
               "ev_to_revenue": ev / rev if rev and rev > 0 else None,
               "ev_per_attributable_unit_usd": ev / geo if geo else None,
               "asset_count": fb.num("portfolio_asset_count", label="Portfolio asset count"),
               "top_asset_share_pct": fb.num("top_asset_share_pct", label="Top asset share"), "primary_metal": primary}
    score = None
    bench = [b for b in (benchmarks or []) if b.kind == "peer_pcf" and b.stage == "royalty" and b.metal == primary]
    if bench and metrics["price_to_cash_flow"]:
        b, x = bench[0], metrics["price_to_cash_flow"]
        score = interp(x, [(0.0, 100), (b.p25, 80), (b.median, 55), (b.p75, 30), (2 * b.p75, 0)])
        metrics["peer_benchmark"] = {"p25": b.p25, "median": b.median, "p75": b.p75, "n": b.n, "as_of": str(b.as_of), "source": b.source}
    else:
        ctx.flags.append("no peer price-to-cash-flow benchmarks loaded: royalty valuation is NOT RATED (metrics shown for reference)")
    return _result(ctx, "royalty", "Price to cash flow vs peers", value_score=score, metrics=metrics)
