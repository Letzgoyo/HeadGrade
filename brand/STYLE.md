# HeadGrade brand and colour structure

One source of truth. Colours are defined once as CSS variables (`headgrade/render.py` `CSS` for the base tokens, `headgrade/web/static/site.css` for the grade ramp) and as constants in `headgrade/brand.py`.

## Logo
| File | Use |
|---|---|
| `headgrade-1-core.svg` / `-dark.svg` | Full logo (icon + wordmark) on light / dark backgrounds |
| `headgrade-icon.svg` / `-dark.svg` | The H alone, for headers and small spaces |
| `headgrade-app-icon.svg` / `.png` | Favicon, app icon, X profile picture (`png/headgrade-x-profile-800.png`) |
| `png/` | Ready-made PNG exports |

Rules: keep clear space of one core-piece width around the mark; never recolour the copper block; never stretch; minimum icon height 20px. The wordmark is plain text in the page font until a designer converts it to outlines.

## Colour
| Role | Light | Dark | Used for |
|---|---|---|---|
| Ink (text, headers) | `#14202b` | `#e8ecf2` | Body text, headings, logo wordmark |
| Background | `#ffffff` | `#10151c` | Page |
| Surface (cards, panels) | `#f8f9fb` | `#171e28` | Panels, table hover, footer |
| Hero / warm paper | `#f3f1ec` | `#151d27` | Home hero band |
| Line | `#e4e7ec` | `#2a3340` | Borders, grid lines |
| Muted text | `#667085` | `#98a2b3` | Secondary text |
| **Brand accent (petrol teal)** | `#0e6b7a` | `#5cc0cf` | Links, buttons, logo, data dots |
| **Metal accent (copper)** | `#c8872e` | same | Logo block only. Never used as a status or text colour |

### Reserved status colours (never use for brand or decoration)
| Meaning | Light | Dark |
|---|---|---|
| Pass / verified | `#1a7f5a` | `#47c28f` |
| Caution | `#b7791f` | `#e0a84a` |
| Fail / red flag | `#c0392b` | `#ef6b5b` |
Status always ships with an icon or word (check, cross, "unknown"), never colour alone.

### Grade chips (single-hue ramp, magnitude not good/bad)
A `#0e6b7a`, B `#3f90a0`, C `#8bbac4`, D `#cfe0e4`, F `#e7ebee` (light). Dark mode has its own steps. Letter is always printed in the chip. "Not rated" is a dashed outline with a dash.

## Type
System sans-serif stack (Inter if present). Headings bold with slight negative tracking; body 15px/1.5; numbers use tabular figures.

## Voice
Plain and direct. "Hype fades. Filings don't." No recommendations, no hype words, no price predictions. Say "unknown" when we do not know.
