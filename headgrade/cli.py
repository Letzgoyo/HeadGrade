"""Command line for the pipeline. Run as:  python -m headgrade.cli <command> ..."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from sqlalchemy import select

from . import db, pipeline, valuation as V
from .db import Benchmark, Company, Fact, JurisdictionScore, PriceDeck
from .decks import Deck
from .registry import METALS, STAGES


def _co(s, ticker):
    c = s.scalar(select(Company).where(Company.ticker == ticker.upper()))
    if not c:
        sys.exit(f"No company with ticker {ticker}. Add it with: company add")
    return c


def _kv(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k.strip()] = float(v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="headgrade")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    c = sub.add_parser("company-add")
    c.add_argument("--name", required=True); c.add_argument("--ticker", required=True)
    c.add_argument("--stage", choices=STAGES, required=True); c.add_argument("--metals", default="au")
    c.add_argument("--jurisdiction"); c.add_argument("--currency", default="USD"); c.add_argument("--exchange")
    c.add_argument("--held", action="store_true", help="the site owner holds this stock (disclosed on the card)")
    c.add_argument("--sponsored", action="store_true", help="company sponsors the site (disclosed on the card)")
    sub.add_parser("company-list")
    fr = sub.add_parser("jurisdictions-load-fraser", help="load the Fraser Institute survey tables from its PDF")
    fr.add_argument("pdf"); fr.add_argument("--edition", default="2025")
    fr.add_argument("--first-page", type=int, default=15); fr.add_argument("--last-page", type=int, default=28)
    sub.add_parser("jurisdictions-list")
    d = sub.add_parser("deck-set"); d.add_argument("--name", default="spot"); d.add_argument("--as-of", default=str(date.today()))
    d.add_argument("prices", nargs="+", help="au=4600 ag=52 cu=5.2 ...")
    j = sub.add_parser("jurisdiction-set"); j.add_argument("name"); j.add_argument("score", type=float); j.add_argument("--source", default="manual")
    b = sub.add_parser("benchmark-add"); b.add_argument("--kind", required=True, choices=["peer_ev_per_unit", "precedent_per_unit", "peer_pcf"])
    b.add_argument("--metal", required=True); b.add_argument("--stage", required=True)
    b.add_argument("--p25", type=float, required=True); b.add_argument("--median", type=float, required=True); b.add_argument("--p75", type=float, required=True)
    b.add_argument("--n", type=int, required=True); b.add_argument("--source", required=True); b.add_argument("--as-of", default=str(date.today()))
    i = sub.add_parser("ingest"); i.add_argument("ticker"); i.add_argument("pdf")
    i.add_argument("--kind", default="technical_report"); i.add_argument("--effective"); i.add_argument("--title"); i.add_argument("--url")
    f = sub.add_parser("facts"); f.add_argument("ticker"); f.add_argument("--status", default="unverified")
    for name in ("verify", "reject"):
        v = sub.add_parser(name); v.add_argument("ids", type=int, nargs="+")
    o = sub.add_parser("override"); o.add_argument("id", type=int); o.add_argument("--value", type=float); o.add_argument("--text"); o.add_argument("--note", default="")
    k = sub.add_parser("card"); k.add_argument("ticker"); k.add_argument("--price", type=float, required=True)
    k.add_argument("--currency"); k.add_argument("--price-date", default=str(date.today()))
    k.add_argument("--fx", nargs="*", help="USD per unit, e.g. CAD=0.73"); k.add_argument("--publish", action="store_true")
    k.add_argument("--out"); k.add_argument("--save", action="store_true", help="store this week's snapshot")
    a = ap.parse_args(argv)

    engine = db.make_engine()
    db.init_db(engine)
    with db.session_scope(engine) as s:
        if a.cmd == "init":
            print("database ready")
        elif a.cmd == "company-add":
            s.add(Company(name=a.name, ticker=a.ticker.upper(), stage=a.stage, primary_metals=a.metals, jurisdiction=a.jurisdiction,
                          currency=a.currency, exchange=a.exchange, held_by_owner=a.held, sponsored=a.sponsored))
            print("added", a.ticker.upper())
        elif a.cmd == "company-list":
            for x in s.scalars(select(Company)):
                print(f"{x.ticker:10} {x.stage:9} {x.name}  [{x.primary_metals}]" + ("  HELD" if x.held_by_owner else "") + ("  SPONSOR" if x.sponsored else ""))
        elif a.cmd == "jurisdictions-load-fraser":
            from . import fraser
            res = fraser.load(s, fraser.parse_pdf(a.pdf, a.first_page, a.last_page), a.edition)
            print(res)
        elif a.cmd == "jurisdictions-list":
            for r in s.scalars(select(JurisdictionScore).order_by(JurisdictionScore.score.desc())):
                print(f"{r.score:6.2f}  IAI {r.iai if r.iai is not None else '-':>6}  {r.name}" + ("  (low sample)" if r.low_sample else ""))
        elif a.cmd == "deck-set":
            prices = _kv(a.prices)
            bad = [m for m in prices if m not in METALS]
            if bad:
                sys.exit(f"unknown metals: {bad}; use {METALS}")
            s.add(PriceDeck(name=a.name, as_of=date.fromisoformat(a.as_of), prices_json=json.dumps(prices)))
            print("deck saved", prices)
        elif a.cmd == "jurisdiction-set":
            row = s.scalar(select(JurisdictionScore).where(JurisdictionScore.name == a.name))
            if row:
                row.score, row.source = a.score, a.source
            else:
                s.add(JurisdictionScore(name=a.name, score=a.score, source=a.source))
        elif a.cmd == "benchmark-add":
            s.add(Benchmark(kind=a.kind, metal=a.metal, stage=a.stage, p25=a.p25, median=a.median, p75=a.p75, n=a.n,
                            as_of=date.fromisoformat(a.as_of), source=a.source))
        elif a.cmd == "ingest":
            import anthropic
            from .ingest import ingest_pdf
            co = _co(s, a.ticker)
            r = ingest_pdf(s, co, a.pdf, a.kind, anthropic.Anthropic(), date.fromisoformat(a.effective) if a.effective else None, a.title, a.url)
            print(r)
        elif a.cmd == "facts":
            q = select(Fact).where(Fact.company_id == _co(s, a.ticker).id)
            if a.status != "all":
                q = q.where(Fact.status == a.status)
            for x in s.scalars(q.order_by(Fact.key, Fact.id)):
                val = x.value_text if x.value_text is not None else x.value_num
                print(f"#{x.id:<4} {x.key:28} {x.metal or '':5} {x.category or '':18} {val!s:>16} {x.unit or '':5} {x.currency or '':3} "
                      f"p.{x.page} [{x.auto_check}] {x.status}\n        \"{(x.quote or '')[:110]}\"" + (f"\n        note: {x.note}" if x.note else ""))
        elif a.cmd in ("verify", "reject"):
            for fid in a.ids:
                x = s.get(Fact, fid)
                x.status = "verified" if a.cmd == "verify" else "rejected"
                x.verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
            print(a.cmd, "ok", a.ids)
        elif a.cmd == "override":
            x = s.get(Fact, a.id)
            old = x.value_text if x.value_text is not None else x.value_num
            if a.value is not None:
                x.value_num = a.value
            if a.text is not None:
                x.value_text = a.text
            x.status, x.verified_at = "override", datetime.now(timezone.utc).replace(tzinfo=None)
            x.note = f"{x.note or ''} [overridden by reviewer; extracted value was {old}] {a.note}".strip()
        elif a.cmd == "card":
            co = _co(s, a.ticker)
            deck = pipeline.latest_deck(s)
            if not deck:
                sys.exit("Set a price deck first: deck-set au=... ")
            ccy = a.currency or co.currency
            fx = {"USD": 1.0, **_kv(a.fx)}
            mk = V.Market(a.price, ccy, date.fromisoformat(a.price_date), fx)
            card = pipeline.build_card(s, co, deck, mk, publish=a.publish)
            changes = pipeline.diff_cards(pipeline.last_snapshot(s, co), card)
            if a.save:
                pipeline.save_snapshot(s, co, card)
            from .render import render_html
            html = render_html(card, changes)
            if a.out:
                open(a.out, "w").write(html)
                print("card ->", a.out)
            g = card["grade"]
            print(f"{co.name}: quality {g['quality_grade']}  valuation {g['value_grade']}  confidence {g['confidence']['level']}  ({card['mode']})")
            print("\n".join("  - " + x for x in changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
