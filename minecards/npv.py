"""A deliberately simple project cash-flow model, calibrated to the company's own published NPV.

We do not know the study's cost assumptions, but we know its result. So: model the revenue from
stated production and the study's price deck, model capex, tax and royalties simply, then solve for
the single annual operating cost that reproduces the published after-tax NPV. With that cost fixed,
we can ask what the NPV becomes at today's prices or a different discount rate.

Simplifications (stated on the card): flat production, straight-line depreciation, no loss
carry-forward, no working capital or closure costs, year-end discounting from the start of construction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ProjectInputs:
    production: dict[str, float]      # metal -> payable units per year
    mine_life: float                  # years
    construction_years: int = 2
    initial_capex: float = 0.0
    tax_rate: float = 0.25
    royalty_pct: float = 0.0          # fraction of revenue
    discount_rate: float = 0.05


def revenue(inp: ProjectInputs, prices: dict[str, float]) -> float:
    return sum(q * prices[m] for m, q in inp.production.items())


def cashflows(inp: ProjectInputs, prices: dict[str, float], annual_opex: float) -> list[float]:
    C = max(1, int(inp.construction_years))
    n_ops = math.ceil(inp.mine_life)
    rev = revenue(inp, prices)
    dep_per_year = inp.initial_capex / inp.mine_life if inp.mine_life else 0.0
    cfs = [-inp.initial_capex / C] * C
    for i in range(n_ops):
        frac = min(1.0, inp.mine_life - i)
        r = rev * frac
        roy = r * inp.royalty_pct
        opex = annual_opex * frac
        taxable = r - roy - opex - dep_per_year * frac
        tax = max(0.0, taxable) * inp.tax_rate
        cfs.append(r - roy - opex - tax)
    return cfs


def npv(inp: ProjectInputs, prices: dict[str, float], annual_opex: float, rate: float | None = None) -> float:
    r = inp.discount_rate if rate is None else rate
    return sum(cf / (1 + r) ** (t + 1) for t, cf in enumerate(cashflows(inp, prices, annual_opex)))


def calibrate_opex(inp: ProjectInputs, study_prices: dict[str, float], target_npv: float) -> tuple[float | None, str]:
    """Annual operating cost that reproduces `target_npv` at the study's own prices and discount rate."""
    hi = max(revenue(inp, study_prices) * 3, 1.0)
    if npv(inp, study_prices, 0.0) < target_npv:
        return None, "published NPV is higher than the model can reach even with zero operating cost: check the inputs"
    if npv(inp, study_prices, hi) > target_npv:
        return None, "published NPV is lower than the model gives even with very high costs: check the inputs"
    lo = 0.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(inp, study_prices, mid) > target_npv:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, "ok"


def nav_equity(inp: ProjectInputs, opex: float, prices: dict[str, float], ownership: float,
               net_cash: float, rate: float | None = None) -> float:
    """Attributable project NPV plus net cash. (No value for corporate costs or other assets.)"""
    return ownership * npv(inp, prices, opex, rate) + net_cash


def solve_multiplier(value_at, target: float, lo: float = 0.05, hi: float = 10.0) -> tuple[float, str]:
    """Smallest-effort bisection: the price multiplier m where value_at(m) == target (value_at must rise with m)."""
    if value_at(lo) > target:
        return lo, f"even at {lo:g}x the deck the value is above the target"
    if value_at(hi) < target:
        return hi, f"even at {hi:g}x the deck the value is below the target"
    for _ in range(200):
        mid = (lo + hi) / 2
        if value_at(mid) > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2, "ok"


def implied_price_multiplier(inp: ProjectInputs, opex: float, base_prices: dict[str, float], ownership: float,
                             net_cash: float, market_cap: float, fair_multiple: float = 1.0,
                             rate: float | None = None) -> tuple[float, str]:
    """The factor m on `base_prices` at which NAV(m) * fair_multiple equals the market cap.
    Reads as: 'the market is pricing this project as if metals were m x the deck'."""
    value_at = lambda m: nav_equity(inp, opex, {k: v * m for k, v in base_prices.items()}, ownership, net_cash, rate) * fair_multiple
    m, why = solve_multiplier(value_at, market_cap)
    if why != "ok":
        why = "market cap is below this project's NAV even at 5% of the deck prices" if m <= 0.05 + 1e-9 else "market cap exceeds this project's NAV even at 10x the deck prices"
    return m, why
