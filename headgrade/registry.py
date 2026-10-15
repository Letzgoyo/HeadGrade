"""The controlled vocabulary of facts. Extraction can only emit these keys; valuation and grading
read only these keys. Adding a metric means adding it here first."""
from __future__ import annotations

from dataclasses import dataclass

METALS = ("au", "ag", "cu", "ni", "u3o8", "zn", "pb", "pt", "pd")
METAL_UNIT = {"au": "oz", "ag": "oz", "pt": "oz", "pd": "oz", "cu": "lb", "ni": "lb", "u3o8": "lb", "zn": "lb", "pb": "lb"}
RESOURCE_CATEGORIES = ("measured", "indicated", "measured_indicated", "inferred")
RESERVE_CATEGORIES = ("proven", "probable", "proven_probable")
STAGES = ("explorer", "developer", "producer", "royalty")
STUDY_STAGES = ("none", "PEA", "PFS", "FS", "construction", "production")
PERMIT_STAGES = ("none", "baseline", "in_process", "approved", "fully_permitted")


@dataclass(frozen=True)
class FactSpec:
    key: str
    family: str   # currency | count | pct | metal_qty | tonnes | grade | price | years | text | date | ratio
    scope: str    # company | project
    description: str
    needs_metal: bool = False
    needs_category: bool = False


def _s(key, family, scope, description, needs_metal=False, needs_category=False):
    return key, FactSpec(key, family, scope, description, needs_metal, needs_category)


REGISTRY: dict[str, FactSpec] = dict([
    # ---- company: balance sheet and share structure
    _s("shares_basic", "count", "company", "Basic shares outstanding"),
    _s("shares_fully_diluted", "count", "company", "Fully diluted shares (options, warrants, convertibles included), if stated"),
    _s("cash", "currency", "company", "Cash and cash equivalents (plus short-term investments if reported together)"),
    _s("debt", "currency", "company", "Total debt: borrowings, convertible notes, credit facilities (excluding trade payables)"),
    _s("quarterly_cash_burn", "currency", "company", "Cash used in operations plus exploration/development spend for the latest quarter, as a positive number"),
    # ---- company: producer
    _s("revenue_ttm", "currency", "company", "Revenue, trailing twelve months or latest fiscal year"),
    _s("ebitda_ttm", "currency", "company", "Adjusted EBITDA, trailing twelve months or latest fiscal year"),
    _s("op_cash_flow_ttm", "currency", "company", "Cash flow from operations, trailing twelve months or latest fiscal year"),
    _s("capex_ttm", "currency", "company", "Total capital expenditures (sustaining plus growth), same period"),
    _s("production_ttm", "metal_qty", "company", "Metal produced or sold in the same period", needs_metal=True),
    _s("aisc_per_unit", "price", "company", "All-in sustaining cost per ounce or pound (state basis in the quote)", needs_metal=True),
    _s("realized_price", "price", "company", "Average realized price per ounce or pound", needs_metal=True),
    _s("reserve_life_years", "years", "company", "Reserve life or mine life at current production"),
    _s("tax_rate", "pct", "company", "Statutory or effective income tax rate stated"),
    # ---- company: royalty / streaming
    _s("attributable_production_ttm", "metal_qty", "company", "Attributable production or gold-equivalent ounces from royalties/streams", needs_metal=True),
    _s("royalty_revenue_ttm", "currency", "company", "Royalty and stream revenue, trailing twelve months or latest fiscal year"),
    _s("portfolio_asset_count", "count", "company", "Number of royalty/stream assets in the portfolio"),
    _s("top_asset_share_pct", "pct", "company", "Share of revenue or NAV from the largest asset"),
    # ---- company: team and governance (only what the documents state)
    _s("team.prior_mines_built", "count", "company", "Number of mines/projects management has previously built or taken into production (as stated)"),
    _s("team.insider_ownership_pct", "pct", "company", "Percentage of shares held by directors and officers"),
    _s("team.regulatory_actions", "count", "company", "Number of disclosed regulatory or securities actions against the company or its officers"),
    _s("team.track_record_note", "text", "company", "A sourced statement about an officer/director's previous roles or outcomes"),
    # ---- project
    _s("study_stage", "text", "project", "Most advanced completed study: PEA, PFS, FS, or none"),
    _s("study_date", "date", "project", "Effective date of the economic study"),
    _s("study_price", "price", "project", "Metal price assumed in the economic study", needs_metal=True),
    _s("npv_after_tax", "currency", "project", "After-tax NPV stated in the study (100% project basis)"),
    _s("npv_discount_rate", "pct", "project", "Discount rate used for the stated NPV"),
    _s("irr_after_tax", "pct", "project", "After-tax IRR"),
    _s("initial_capex", "currency", "project", "Initial (pre-production) capital cost"),
    _s("mine_life_years", "years", "project", "Life of mine in years"),
    _s("construction_years", "years", "project", "Construction period in years"),
    _s("annual_production", "metal_qty", "project", "Average annual payable metal production over the mine life", needs_metal=True),
    _s("royalty_burden_pct", "pct", "project", "Royalties/streams as a percentage of revenue, if stated"),
    _s("ownership_pct", "pct", "project", "Company's attributable interest in the project"),
    _s("project_weight_pct", "pct", "project", "Share of the company's production, revenue or NAV that comes from this project, as stated"),
    _s("resource_contained", "metal_qty", "project", "Contained metal in mineral resources", needs_metal=True, needs_category=True),
    _s("reserve_contained", "metal_qty", "project", "Contained metal in mineral reserves", needs_metal=True, needs_category=True),
    _s("resource_grade", "grade", "project", "Average grade of mineral resources", needs_metal=True, needs_category=True),
    _s("recovery", "pct", "project", "Metallurgical recovery assumed", needs_metal=True),
    _s("permit_stage", "text", "project", "Permitting status: none, baseline, in_process, approved, fully_permitted"),
    _s("jurisdiction", "text", "project", "Country / state / province of the project"),
    _s("expansion_potential", "text", "project", "Stated expansion or exploration upside (quote the company's own words)"),
    _s("cutoff_grade", "grade", "project", "Cutoff grade used to report the mineral resource or reserve", needs_metal=True),
    # ---- project checklist statements: value text is "yes" or "no"; the quote must be the sentence that supports it
    _s("check.simple_metallurgy", "text", "project", "yes if the document says processing is conventional/proven/simple (e.g. standard gravity+CIL, high recoveries, no refractory ore or penalty elements); no if it says the ore is refractory, complex or carries penalty elements"),
    _s("check.strategic_partner", "text", "project", "yes if a strategic partner, joint-venture partner or strategic investor is involved in the project or company, as stated"),
    _s("check.partner_is_major", "text", "project", "yes if that partner is a major, mid-tier or otherwise well-funded operating mining company, as stated"),
    _s("check.community_support", "text", "project", "yes if a community, First Nations or local agreement or stated community support is disclosed"),
    _s("check.no_opposition", "text", "project", "yes only if the document states there is no significant community opposition, blockade or legal challenge; no if it discloses one"),
    _s("check.water_ok", "text", "project", "yes if the document says water supply is secure and water/contamination is not a material issue; no if it names water as a risk or constraint"),
    _s("check.permitting_pathway", "text", "project", "yes if the document describes a clear, supportive or fast-track permitting pathway; no if it describes delays, uncertainty or opposition"),
    _s("check.low_cost_mining", "text", "project", "yes if the mining method is low-cost and low-dilution (e.g. bulk open pit, selective high-grade zones); no if it is high-cost or high-dilution"),
    _s("check.baseline_studies", "text", "project", "yes if environmental baseline studies are completed or under way"),
    _s("check.infrastructure_ok", "text", "project", "yes if power, road, rail, port and water infrastructure are described as available or nearby; no if major infrastructure must be built"),
])
