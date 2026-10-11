"""Plain, dependency-free HTML for a card. (The public site will wrap this in its own templates.)"""
from __future__ import annotations

from html import escape

CSS = """
:root{--bg:#fff;--fg:#1c2430;--mut:#667085;--line:#e4e7ec;--card:#f8f9fb;--good:#1a7f5a;--warn:#b7791f;--bad:#c0392b;--acc:#2a62d9}
@media(prefers-color-scheme:dark){:root{--bg:#10151c;--fg:#e8ecf2;--mut:#98a2b3;--line:#2a3340;--card:#171e28;--good:#47c28f;--warn:#e0a84a;--bad:#ef6b5b;--acc:#7da4ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:900px;margin:0 auto;padding:24px 16px 64px}h1{margin:0;font-size:26px}h2{font-size:17px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.sub{color:var(--mut)}.draft{background:#fff4d6;color:#7a5600;border:1px solid #f0d58c;padding:8px 12px;border-radius:8px;margin:12px 0}
.grades{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}.g{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;min-width:150px}
.g b{font-size:34px;line-height:1}.g span{display:block;color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.flag{border-left:4px solid var(--warn);background:var(--card);padding:6px 10px;margin:6px 0;border-radius:4px}.flag.high{border-color:var(--bad)}
.mut{color:var(--mut)}.small{font-size:12.5px}.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}
.pill.ok{color:var(--good);border-color:var(--good)}.pill.bad{color:var(--bad);border-color:var(--bad)}q{color:var(--mut)}
"""


def usd(x, d=1):
    if x is None:
        return "n/a"
    a = abs(x)
    s = f"{a / 1e9:,.{d}f}B" if a >= 1e9 else f"{a / 1e6:,.{d}f}M" if a >= 1e6 else f"{a:,.0f}"
    return ("-" if x < 0 else "") + "$" + s


def num(x, d=2):
    return "n/a" if x is None else f"{x:,.{d}f}"


def pct(x, d=0):
    return "n/a" if x is None else f"{x:.{d}f}%"


def fmt_value(e: dict) -> str:
    """Human-readable input value: as written in the document if we kept it, otherwise formatted from the number."""
    if e.get("value") not in (None, "") and isinstance(e["value"], str) and not e["value"].replace(".", "").replace("-", "").isdigit():
        return e["value"]
    v = e.get("value_raw")
    if not isinstance(v, (int, float)):
        return str(e.get("value"))
    if e.get("currency") or (e.get("unit") is None and abs(v) >= 1e6):
        return usd(v)
    unit = e.get("unit") or ""
    return f"{v:,.2f}".rstrip("0").rstrip(".") + (" " + unit if unit else "")


def _rows(pairs):
    return "".join(f"<tr><th>{escape(k)}</th><td class='n'>{v}</td></tr>" for k, v in pairs if v is not None)


def _valuation_section(v: dict, deck: dict) -> str:
    m, stage = v["metrics"], v["stage"]
    out = [f"<h2>Valuation ({escape(stage)}): {escape(v['method'])}</h2>"]
    if stage == "developer":
        if "p_nav" in m:
            lo_hi = m.get("typical_p_nav_range")
            out.append("<table>" + _rows([
                ("Market cap", usd(m["market_cap_usd"])), ("Net cash", usd(m["net_cash_usd"])),
                ("NAV at the current deck (published discount rate)", usd(m["nav_usd"])),
                ("NAV per share", ("$" + num(m["nav_per_share_usd"])) if m["nav_per_share_usd"] else None),
                ("Price / NAV (today's share count)", num(m["p_nav"]) + "x"),
                ("Pro-forma price / NAV after funding the capex with new shares", (num(m["pro_forma_p_nav"]) + "x") if m.get("pro_forma_p_nav") else None),
                ("Pro-forma NAV per share", ("$" + num(m["pro_forma_nav_per_share_usd"])) if m.get("pro_forma_nav_per_share_usd") else None),
                ("Price / NAV at an 8% discount rate", (num(m["p_nav_at_8pct"]) + "x") if m.get("p_nav_at_8pct") else None),
                (f"Typical for a {m.get('study_stage')}-stage developer (rule of thumb)", f"{lo_hi[0]:.2f}x to {lo_hi[1]:.2f}x" if lo_hi else None),
                (f"Metal price the market implies (1.0x NAV), {m['primary_metal'].upper()}",
                 f"${num(m['implied_primary_price_at_1x_nav'], 0)} vs ${num(deck['prices'].get(m['primary_metal']), 0)} deck" if m.get("implied_primary_price_at_1x_nav") else None),
                (f"Metal price implied at the middle of the typical range, {m['primary_metal'].upper()}",
                 f"${num(m['implied_primary_price_at_fair_midpoint'], 0)}" if m.get("implied_primary_price_at_fair_midpoint") else None),
                ("Funding gap (capex less cash)", usd(m.get("funding_gap_usd"))),
                ("Dilution if funded entirely with new shares", pct(m.get("dilution_if_all_equity_pct")) if m.get("dilution_if_all_equity_pct") is not None else None),
            ]) + "</table>")
        projects = v["tables"].get("projects", [])
        if projects:
            out.append("<table><tr><th>Project</th><th class='n'>Published NPV</th><th class='n'>NPV at deck</th><th class='n'>at 8%</th><th class='n'>at 80% of deck</th><th>Status</th></tr>")
            for p in projects:
                out.append(f"<tr><td>{escape(p['project'])}</td><td class='n'>{usd(p.get('published_npv_usd'))}</td><td class='n'>{usd(p.get('npv_at_deck'))}</td>"
                           f"<td class='n'>{usd(p.get('npv_at_deck_8pct'))}</td><td class='n'>{usd(p.get('npv_at_80pct_deck'))}</td><td>{escape(p['status'])}</td></tr>")
            out.append("</table>")
        sens = v["tables"].get("sensitivity")
        if sens:
            out.append("<p class='mut small'>Sensitivity: price-to-NAV at different metal prices (as a multiple of the deck) and discount rates</p>"
                       "<table><tr><th>Metal price vs deck</th><th class='n'>P/NAV (published rate)</th><th class='n'>P/NAV at 8%</th><th class='n'>P/NAV at 10%</th></tr>")
            for r in sens:
                out.append(f"<tr><td>{round(r['price_vs_deck'] * 100)}%</td><td class='n'>{num(r.get('p_nav_published_rate'))}x</td>"
                           f"<td class='n'>{num(r.get('p_nav_at_8pct'))}x</td><td class='n'>{num(r.get('p_nav_at_10pct'))}x</td></tr>")
            out.append("</table>")
    elif stage == "explorer":
        out.append("<table>" + _rows([
            ("Market cap", usd(m.get("market_cap_usd"))), ("Enterprise value", usd(m.get("ev_usd"))), ("Cash", usd(m.get("cash_usd"))),
            (f"Measured and indicated ({m.get('primary_metal', '').upper()}-equivalent)", num(m.get("mi_equivalent_units"), 0)),
            (f"Inferred ({m.get('primary_metal', '').upper()}-equivalent)", num(m.get("inferred_equivalent_units"), 0)),
            ("EV per equivalent unit (inferred weighted 50%)", ("$" + num(m.get("ev_per_weighted_unit_usd"))) if m.get("ev_per_weighted_unit_usd") else None),
            ("Cash runway", (f"{m['runway_months']:.0f} months") if m.get("runway_months") is not None else None),
            ("Share of resource that is measured/indicated", pct(100 * m["mi_share_of_resource"]) if m.get("mi_share_of_resource") is not None else None),
            ("Peer benchmark (25th / median / 75th percentile)",
             (f"${m['peer_benchmark']['p25']:,.0f} / ${m['peer_benchmark']['median']:,.0f} / ${m['peer_benchmark']['p75']:,.0f} (n={m['peer_benchmark']['n']})") if m.get("peer_benchmark") else None),
        ]) + "</table>")
    elif stage == "producer":
        out.append("<table>" + _rows([
            ("Market cap", usd(m.get("market_cap_usd"))), ("Net debt", usd(m.get("net_debt_usd"))),
            ("Free cash flow (reported period)", usd(m.get("fcf_ttm_usd"))),
            ("FCF yield, reported", pct(100 * m["fcf_yield_reported"], 1) if m.get("fcf_yield_reported") is not None else None),
            ("FCF yield, normalised to the deck", pct(100 * m["fcf_yield_normalised"], 1) if m.get("fcf_yield_normalised") is not None else None),
            ("FCF yield if metals fall 20%", pct(100 * m["fcf_yield_at_stress"], 1) if m.get("fcf_yield_at_stress") is not None else None),
            ("Operating cash flow (reported period)", usd(m.get("operating_cash_flow_usd")) if m.get("operating_cash_flow_usd") else None),
            ("Price / operating cash flow", (num(m.get("price_to_operating_cash_flow"), 1) + "x") if m.get("price_to_operating_cash_flow") else None),
            ("EV / revenue", (num(m.get("ev_to_revenue"), 1) + "x") if m.get("ev_to_revenue") else None),
            ("EV / EBITDA", (num(m.get("ev_to_ebitda"), 1) + "x") if m.get("ev_to_ebitda") else None),
            ("Net debt / EBITDA", (num(m.get("net_debt_to_ebitda"), 1) + "x") if m.get("net_debt_to_ebitda") is not None else None),
            ("AISC margin at the deck price", pct(100 * m["aisc_margin_at_deck"]) if m.get("aisc_margin_at_deck") is not None else None),
            ("Reserve life", (num(m.get("reserve_life_years"), 1) + " years") if m.get("reserve_life_years") else None),
        ]) + "</table>")
    else:
        out.append("<table>" + _rows([
            ("Market cap", usd(m.get("market_cap_usd"))), ("Price / cash flow", (num(m.get("price_to_cash_flow"), 1) + "x") if m.get("price_to_cash_flow") else None),
            ("EV / revenue", (num(m.get("ev_to_revenue"), 1) + "x") if m.get("ev_to_revenue") else None),
            ("Assets in portfolio", num(m.get("asset_count"), 0) if m.get("asset_count") is not None else None),
        ]) + "</table>")
    return "".join(out)


def render_html(card: dict, changes: list[str] | None = None) -> str:
    c, g, v = card["company"], card["grade"], card["valuation"]
    draft = card["mode"] != "published"
    conf = g["confidence"]
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{escape(c['name'])} card</title><style>{CSS}</style></head><body><main>",
         f"<h1>{escape(c['name'])}</h1><div class='sub'>{escape(c['ticker'])} &middot; {escape(c['stage'])} &middot; "
         f"{', '.join(m.upper() for m in c['metals'])} &middot; {escape(c.get('jurisdiction') or '')}</div>"]
    if draft:
        h.append("<div class='draft'><b>DRAFT.</b> This preview includes facts that have not been verified by a person. Do not publish.</div>")
    d = card["disclosures"]
    if d["sponsored"] or d["owner_holds_position"]:
        h.append("<div class='flag'><b>Disclosure.</b> " + " ".join(filter(None, [
            "This company is a sponsor of this site." if d["sponsored"] else "",
            "The site owner holds a position in this company." if d["owner_holds_position"] else ""])) + "</div>")
    h.append("<div class='grades'>"
             f"<div class='g'><span>Quality</span><b>{g['quality_grade']}</b><div class='mut small'>{num(g['quality_score'], 0) if g['quality_score'] is not None else 'not enough data'}</div></div>"
             f"<div class='g'><span>Valuation</span><b>{g['value_grade']}</b><div class='mut small'>{num(g['value_score'], 0) if g['value_score'] is not None else 'not rated'}</div></div>"
             f"<div class='g'><span>Confidence</span><b style='font-size:22px'>{escape(conf['level'])}</b><div class='mut small'>{conf['verified']} of {conf['inputs_used']} inputs verified ({conf.get('human_verified', 0)} by a person, {conf.get('auto_verified', 0)} automatically)</div></div></div>")
    h.append(f"<p><b>{escape(g['quadrant'])}.</b> <span class='mut'>Price {card['market']['price']:,.2f} {card['market']['currency']}"
             f" as of {escape(card['market']['price_date'] or 'n/a')}. Metal prices (deck '{escape(card['deck']['name'])}', {escape(card['deck']['as_of'] or 'n/a')}): "
             + ", ".join(f"{k.upper()} {val:,.2f}" for k, val in card["deck"]["prices"].items()) + ".</span></p>")
    if changes:
        h.append("<h2>What changed</h2><ul>" + "".join(f"<li>{escape(x)}</li>" for x in changes) + "</ul>")
    h.append("<h2>Quality</h2><table><tr><th>Category</th><th class='n'>Grade</th><th class='n'>Score</th><th>Why</th></tr>")
    for name, cat in g["categories"].items():
        why = "; ".join(cat["notes"]) or "no data"
        h.append(f"<tr><td>{escape(name.capitalize())}</td><td class='n'>{cat['grade']}</td><td class='n'>{num(cat['score'], 0) if cat['score'] is not None else '-'}</td><td class='small'>{escape(why)}</td></tr>")
    h.append("</table>")
    if g["red_flags"]:
        h.append("<h2>Red flags</h2>" + "".join(f"<div class='flag {x['severity']}'>{escape(x['text'])}</div>" for x in g["red_flags"]))
    h.append(_valuation_section(v, card["deck"]))
    if v["flags"]:
        h.append("<h2>Data notes</h2>" + "".join(f"<div class='flag'>{escape(x)}</div>" for x in v["flags"]))
    if g["missing"]:
        h.append("<h2>What we could not find</h2><p class='small mut'>" + escape(", ".join(g["missing"])) + "</p>")
    h.append("<h2>Assumptions behind this card</h2><ul class='small'>" + "".join(f"<li>{escape(a)}</li>" for a in v["assumptions"]) + "</ul>")
    h.append("<h2>Sources</h2><table><tr><th>Input</th><th>Value</th><th>Check</th><th>Source</th></tr>")
    for e in card["evidence"]:
        ok = e["status"] in ("verified", "override")
        label = "verified (auto)" if e["status"] == "verified" and e.get("verified_by") == "auto" else e["status"]
        src = f"{escape(e['document'] or '?')}, p.{e['page']}" + (f"<br><q class='small'>{escape((e['quote'] or '')[:220])}</q>" if e["quote"] else "")
        h.append(f"<tr><td>{escape(e['label'])}</td><td>{escape(fmt_value(e))}</td><td><span class='pill {'ok' if ok else 'bad'}'>{escape(label)}</span></td><td class='small'>{src}</td></tr>")
    h.append("</table><p class='small mut'>General information and modelled estimates from public documents, not personal financial advice. "
             "Grades are produced by a published method from the inputs shown and can be wrong. Do your own research.</p>"
             f"<p class='small mut'>Generated {escape(card['generated_at'])}</p></main></body></html>")
    return "".join(h)
