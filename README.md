# HeadGrade

Weekly, source-backed valuation cards for mining companies. Each company gets a **quality grade** (team, asset,
jurisdiction, financial strength, growth) and a **separate valuation grade**, with every input traceable to a page of a
company document. Not a live lookup tool: documents are read once, a person verifies the numbers that matter, and
cards are rebuilt weekly.

## How it works
1. **Documents in.** A PDF (technical report, financial statements, MD&A, presentation) is read by Claude into a fixed
   vocabulary of facts (`headgrade/registry.py`). Each fact carries a **verbatim quote and page**.
2. **Checked in code, not by the model.** We confirm the quote really appears on that page and that the number is in the
   quote. Anything we cannot find is **rejected automatically**. Everything else waits as *unverified*.
3. **You verify.** `review_app.py` shows each number beside its quote and the page text: Verify, Reject, or correct it.
   Published cards use **only verified facts**; drafts also use unverified ones and say so.
4. **Valuation by stage** (`valuation.py`, assumptions in `assumptions.py`):
   - *Developer:* NAV from the company's own study, re-priced at your metal deck. The model is calibrated so it reproduces the
     published after-tax NPV at the study's own prices, then flexed (price, discount rate). Shows P/NAV, **pro-forma P/NAV after
     funding the capex with new shares**, the metal price the market is implying, funding gap and dilution, sensitivity table.
   - *Explorer:* EV per equivalent resource unit against peer benchmarks (**not rated until benchmarks are loaded**).
   - *Producer:* normalised free cash flow yield, EV/EBITDA, stress test at -20% metals, AISC margin, net debt/EBITDA.
   - *Royalty:* price/cash flow vs peers (**not rated until benchmarks are loaded**).
5. **Grades** (`grading.py`) with confidence, red flags, and a list of what could not be found. Weekly snapshots and a
   plain-English "what changed" diff (`pipeline.py`).

## Run it
```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...           # only needed to read documents
python -m headgrade.cli init
python -m headgrade.cli deck-set au=... ag=... cu=...        # you set the metal prices; there are no built-in ones
python -m headgrade.cli company-add --name "..." --ticker XXX.TO --stage developer --metals au,ag --jurisdiction Nevada --held
streamlit run review_app.py            # add documents, review facts, preview cards
pytest                                 # 80 tests, all on FICTIONAL data (examples/demo_data.py)
python examples/make_demo_card.py card.html
```

## Automation (the weekly job)
One command per week, no manual document handling:
```
python -m headgrade.cli weekly [--ticker BTG ...]
```
For each company it: (1) finds new SEC filings (6-K, 40-F, 20-F, 10-K, 10-Q), (2) downloads each document and classifies it
(technical report, MD&A, financial statements, results release, annual information form), (3) reads it with Claude, (4) checks every
quote is on the page, (5) reads it AGAIN with a different model and compares, (6) cross-checks numbers across documents and
arithmetic (measured + indicated = M&I, IRR vs NPV vs discount rate, sane ranges), (7) **auto-verifies** a fact only when an
independent check agrees and nothing contradicts it, (8) infers the company's stage and metals from its facts, (9) rebuilds the card
from verified facts only and records what changed. A person only sees conflicts, failed range checks, and random spot-checks
(`python -m headgrade.cli queue`, or the review screen). Spot-checks measure the machine's real error rate
(`audit-sample`, `audit-stats`). A failed spot-check pulls that fact back out of published cards.

Needs two secrets: `ANTHROPIC_API_KEY` and `SEC_USER_AGENT` (your name and email; the SEC requires it).

**B2Gold, start to finish (on Replit):**
```
python -m headgrade.cli init
python -m headgrade.cli company-add-sec BTG --held
python -m headgrade.cli deck-set au=<today's gold price>
python -m headgrade.cli price-set BTG <share price> USD
python -m headgrade.cli jurisdictions-load-fraser <path to the Fraser survey PDF>      # once a year
python -m headgrade.cli weekly --ticker BTG
python -m headgrade.cli queue --ticker BTG
streamlit run review_app.py
```
Other commands: `universe-scan-sec` (find mining companies among SEC filers), `company-set-cik`, `price-set`, `fx-set`, `fx-fetch-boc`.

**Coverage today:** companies that file with the SEC (many TSX/ASX names that cross-list file 40-F/6-K). Companies listed only on
SEDAR+ (most TSX-V/CSE juniors) or only on the ASX are NOT covered yet: SEDAR+ has no official API and its terms must be checked.

## Project checklist
A separate 5x5 panel on every card (24 yes/no tests plus a free square): grade vs cutoff, mine life, processing, jurisdiction, study economics,
management, partners, community, measured share, water, permits, infrastructure, financing, cash and expansion upside. Each square is yes, no,
unknown (nothing sourced found: never a guess) or not applicable to the stage. Text-based squares need a verbatim quote and go through the same two-reading
verification as every other fact. It does not change the grades. Thresholds are in `checklist.THRESHOLDS`; the two "HeadGrade view" squares stay unknown
until a per-metal view is supplied. Royalty companies are not scored this way.

## Jurisdiction scores (Fraser Institute)
`python -m headgrade.cli jurisdictions-load-fraser <Fraser Annual Survey of Mining Companies PDF>` loads all 68 jurisdictions
(tested exactly against the 2025 edition). The **policy score (PPI, 0-100)** drives the jurisdiction grade, because mineral
potential is judged separately at the asset level; the overall Investment Attractiveness Index is stored alongside. Rows the
survey marks as low-sample (5-9 responses) are flagged. For multi-mine companies the score is weighted by each project's stated
share of production/NAV, and 20%+ exposure to a jurisdiction scoring under 40 raises a red flag. Credit Fraser on any public page.

## The public website
`python -m headgrade.cli serve` (development) or `gunicorn -b 0.0.0.0:8080 headgrade.wsgi:app` (what `.replit` runs). It is read-only and shows only
**published** cards (built from verified facts); drafts and the review tools are never reachable. Pages: home, company directory (search, metal, stage,
sort, quality-vs-valuation map), one page per company (card, scorecard, sources, grade history), methodology, about, disclosures and terms, plus
`/sitemap.xml`, `/robots.txt` and `/healthz`. Legal pages are marked DRAFT until you set `HEADGRADE_LEGAL_DRAFT=0` after a lawyer has reviewed them.
Set `HEADGRADE_SITE_URL=https://yourdomain` for correct canonical and sitemap links. Brand and colour rules: `brand/STYLE.md`.
Preview with fictional data: `python examples/seed_site_demo.py demo_out` then `DATABASE_URL=sqlite:///demo_out/demo.db python -m headgrade.cli serve`.

**Admin screen:** `ADMIN_PASSWORD=... streamlit run review_app.py --server.port 8501` in the workspace shell. Do not publish it in the deployment, and always set
`ADMIN_PASSWORD`. Public deployments need a real database (`DATABASE_URL`, Postgres) rather than a local file, because Replit deployments do not keep files.

## Going live on Replit (checklist)
1. Import the repo; Shell: `pip install -r requirements.txt`.
2. Secrets: `ANTHROPIC_API_KEY`, `SEC_USER_AGENT` ("Name email"), `DATABASE_URL` (a Postgres URL, same value in the workspace and the deployment), `ADMIN_PASSWORD`,
   `HEADGRADE_SITE_URL` (your domain, once you have one).
3. Shell: `python -m headgrade.cli init`, then the B2Gold commands below, so there is at least one published card.
4. Press Run to preview the public site; then Deploy (Autoscale), using the run command in `.replit`. Never deploy `review_app.py`.
5. Weekly refresh: run `python -m headgrade.cli weekly` from the workspace shell (or a Scheduled Deployment) against the same `DATABASE_URL`.

## Run on Replit (first live test: B2Gold)
1. Create Repl > Import from GitHub > `Letzgoyo/HeadGrade`. In the Shell: `pip install -r requirements.txt`.
2. Secrets: `ANTHROPIC_API_KEY`, and `SEC_USER_AGENT` = `Your Name your@email.com` (SEC requires it).
3. Upload the Fraser PDF, then in the Shell:
```
python -m headgrade.cli init
python -m headgrade.cli company-add-sec BTG --held
python -m headgrade.cli deck-set au=<today's gold price>
python -m headgrade.cli price-set BTG <US$ share price> USD
python -m headgrade.cli jurisdictions-load-fraser <fraser.pdf>
python -m headgrade.cli weekly --ticker BTG --since-days 250 --dry-run          # free: only lists what it would read
python -m headgrade.cli weekly --ticker BTG --since-days 250 --max-total-docs 6 # small paid run (cap)
python -m headgrade.cli queue --ticker BTG
```
4. To see the review screen: `streamlit run review_app.py --server.port 8501` (the **Run** button starts the public site on port 8080). Re-run the capped `weekly` command to continue where the cap stopped.

## Claude API key
Only needed to read documents. Create a key at console.anthropic.com and store it as a secret named `ANTHROPIC_API_KEY`
(on Replit: the Secrets tool, never in code or chat). Costs are per document read; see the cost notes in the project plan.

## Next
ASX and Canadian (SEDAR+) document sources, a licensed price/metal feed, scheduling (Replit Scheduled Deployment), and peer
benchmarks computed from the companies we cover.

## Honest limits
- **Never run against live SEC data or the live Claude API yet.** Built and tested with recorded-format fixtures, fake SEC responses
  and fake readers. Expect small fixes on the first real run (SEC response formats, HTML tables, model output).
- The **price-to-NAV ranges by study stage and the producer anchors are rule-of-thumb placeholders** (`assumptions.py`),
  printed on every card. Replace them with measured peer data.
- Explorers and royalty companies cannot be valued until peer benchmarks exist in the database.
- Growth is not scored yet. Team scoring uses only facts the documents state.
- Table extraction from PDFs can misalign columns; that is why a person verifies the critical inputs.
- Prices, FX and metal decks are entered by hand for now. A licensed price feed is needed before charging anyone.
- Not financial advice. Get legal advice before publishing grades on specific securities.
