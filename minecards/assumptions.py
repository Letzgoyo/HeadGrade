"""Every judgment call in one place, labelled as such. These are rule-of-thumb starting points,
NOT measured facts: replace them with peer data as the database grows. The card prints them."""

# Typical price-to-NAV ranges for developers by most-advanced study (low, high). Rule of thumb only.
DEVELOPER_PNAV = {
    "PEA": (0.20, 0.40),
    "PFS": (0.30, 0.50),
    "FS": (0.45, 0.70),
    "construction": (0.65, 0.95),
}
# Producer anchors: (value scoring 0, value scoring 100)
PRODUCER_FCF_YIELD = (0.03, 0.10)     # normalised FCF yield on market cap
PRODUCER_EV_EBITDA = (10.0, 4.0)      # EV/EBITDA, lower is better
# Equity issue discount used for the funding-gap dilution estimate
ISSUE_DISCOUNT = 0.10
INFERRED_WEIGHT = 0.5                 # weight on inferred ounces when valuing explorers
PRICE_STRESS = 0.20                   # producer stress test: metal prices fall by this much
SENSITIVITY_PRICE = (0.70, 0.85, 1.00, 1.15, 1.30)
SENSITIVITY_RATES = (0.08, 0.10)
DEFAULT_CONSTRUCTION_YEARS = 2
DEFAULT_TAX_RATE = 0.25

TEXT = {
    "DEVELOPER_PNAV": "Typical market price-to-NAV for developers by study stage: PEA 0.20-0.40, PFS 0.30-0.50, FS 0.45-0.70, "
                      "construction 0.65-0.95. Rule of thumb, not measured peer data.",
    "PRODUCER": "Producer value anchors: normalised free cash flow yield of 3% scores 0 and 10% scores 100; EV/EBITDA of 10 scores 0 and 4 scores 100.",
    "NPV_MODEL": "Project NPV is re-created from stated production, capex, tax and discount rate, then calibrated so it reproduces the "
                 "published after-tax NPV at the study's own prices. Simplified: flat production, straight-line depreciation, "
                 "no loss carry-forward, no working capital or closure costs.",
    "SCORING_BASIS": "The valuation score uses the pro-forma price-to-NAV when the company must raise equity: NAV plus the cash raised, "
                     "divided by the enlarged share count. This stops a company that must raise more than its market cap from looking cheap on plain P/NAV.",
    "FUNDING": f"Funding gap assumes the company's share of initial capex is financed entirely with new equity at a "
               f"{int(ISSUE_DISCOUNT * 100)}% discount to the current price. Debt, streams or partners would reduce dilution.",
    "EXPLORER": f"Explorers have no cash flows to model, so value is judged only against peer benchmarks per equivalent unit of "
                f"resource (inferred ounces weighted {INFERRED_WEIGHT}). Without loaded benchmarks the explorer is 'not rated'.",
}
