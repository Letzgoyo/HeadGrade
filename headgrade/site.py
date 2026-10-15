"""The public, read-only site. Shows only PUBLISHED snapshots (built from verified facts); never drafts, never the review tools."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

from flask import Flask, Response, abort, render_template, request, send_from_directory, url_for
from sqlalchemy import func, select

from . import brand, db, grading, pipeline, render
from .db import Company, Fact, Snapshot

BRAND_DIR = Path(__file__).resolve().parent.parent / "brand"
METAL_NAMES = {"au": "Gold", "ag": "Silver", "cu": "Copper", "u3o8": "Uranium", "ni": "Nickel", "zn": "Zinc", "pb": "Lead", "pt": "Platinum", "pd": "Palladium"}
STAGE_NAMES = {"explorer": "Explorer", "developer": "Developer", "producer": "Producer", "royalty": "Royalty / streaming"}
SORTS = {"name": "Name", "quality": "Quality score", "value": "Valuation score", "updated": "Recently updated"}
PAGE_SIZE = 40
TAGLINE = "Hype fades. Filings don't."


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _latest_published():
    """(Company, Snapshot) for each company's newest published snapshot."""
    newest = select(func.max(Snapshot.id).label("sid")).where(Snapshot.published.is_(True)).group_by(Snapshot.company_id).subquery()
    return select(Company, Snapshot).join(Snapshot, Snapshot.company_id == Company.id).join(newest, newest.c.sid == Snapshot.id)


def _summary(snap: Snapshot) -> dict:
    return json.loads(snap.summary_json) if snap.summary_json else pipeline.summarize(json.loads(snap.card_json))


def _row(co: Company, snap: Snapshot) -> dict:
    s = _summary(snap)
    return {"name": co.name, "ticker": co.ticker, "stage": co.stage, "stage_name": STAGE_NAMES.get(co.stage, co.stage),
            "metals": [m for m in co.primary_metals.split(",") if m], "jurisdiction": co.jurisdiction, "updated": snap.taken_at, **s}


def quality_value_map(rows: list[dict]) -> str:
    """Server-drawn SVG: every rated company as a dot (hover for details, click for the card). No external libraries."""
    pts = [r for r in rows if r.get("quality_score") is not None and r.get("value_score") is not None]
    if not pts:
        return ""
    W, H, L, R, T, B = 620, 440, 56, 16, 14, 52
    pw, ph = W - L - R, H - T - B
    x = lambda q: L + pw * q / 100
    y = lambda v: T + ph * (1 - v / 100)
    o = [f"<svg class='map' viewBox='0 0 {W} {H}' role='img' aria-label='Quality score against valuation score for each rated company'>"]
    for g in (0, 25, 50, 75, 100):
        o.append(f"<line class='gl' x1='{x(g):.1f}' y1='{T}' x2='{x(g):.1f}' y2='{T + ph}'/><line class='gl' x1='{L}' y1='{y(g):.1f}' x2='{L + pw}' y2='{y(g):.1f}'/>"
                 f"<text class='tk' x='{x(g):.1f}' y='{T + ph + 16}' text-anchor='middle'>{g}</text><text class='tk' x='{L - 8}' y='{y(g) + 4:.1f}' text-anchor='end'>{g}</text>")
    o.append(f"<line class='cut' x1='{x(65):.1f}' y1='{T}' x2='{x(65):.1f}' y2='{T + ph}'/><line class='cut' x1='{L}' y1='{y(60):.1f}' x2='{L + pw}' y2='{y(60):.1f}'/>")
    o.append(f"<text class='ql' x='{x(65) + 8:.1f}' y='{T + 16}'>Higher quality, cheaper for its stage</text>")
    o.append(f"<text class='axl' x='{L + pw / 2:.0f}' y='{H - 10}' text-anchor='middle'>Quality score (0 to 100)</text>")
    o.append(f"<text class='axl' transform='translate(14 {T + ph / 2:.0f}) rotate(-90)' text-anchor='middle'>Valuation score (higher = cheaper for its stage)</text>")
    label = len(pts) <= 14
    for r in pts:
        cx, cy = x(r["quality_score"]), y(r["value_score"])
        tip = escape(f"{r['name']} ({r['ticker']}): quality {r['quality_grade']} {r['quality_score']:.0f}, valuation {r['value_grade']} {r['value_score']:.0f}")
        o.append(f"<a href='{url_for('company', ticker=r['ticker'])}'><circle class='pt' cx='{cx:.1f}' cy='{cy:.1f}' r='7'><title>{tip}</title></circle>"
                 f"<circle class='hit' cx='{cx:.1f}' cy='{cy:.1f}' r='14'><title>{tip}</title></circle>"
                 + (f"<text class='pl' x='{cx + 11:.1f}' y='{cy + 4:.1f}'>{escape(r['ticker'])}</text>" if label else "") + "</a>")
    o.append("</svg>")
    return "".join(o)


def create_app(engine=None) -> Flask:
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    eng = engine or db.make_engine()
    db.init_db(eng)
    site_url = os.getenv("HEADGRADE_SITE_URL", "").rstrip("/")
    legal_draft = os.getenv("HEADGRADE_LEGAL_DRAFT", "1") != "0"

    @app.context_processor
    def _ctx():
        return {"brand": brand, "card_css": render.CSS, "tagline": TAGLINE, "metal_names": METAL_NAMES, "stage_names": STAGE_NAMES,
                "legal_draft": legal_draft, "year": date.today().year,
                "base": site_url or request.url_root.rstrip("/")}

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Cache-Control", "public, max-age=300")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    @app.errorhandler(404)
    def _404(_):
        return render_template("page404.html", title="Not found"), 404

    @app.get("/")
    def home():
        with db.session_scope(eng) as s:
            rows = [_row(c, sn) for c, sn in s.execute(_latest_published().order_by(Snapshot.taken_at.desc()).limit(400))]
            n_facts = s.scalar(select(func.count()).select_from(Fact).where(Fact.status.in_(("verified", "override")))) or 0
        stats = {"companies": len(rows), "facts": n_facts, "updated": max((r["updated"] for r in rows), default=None)}
        return render_template("home.html", title="HeadGrade | Mining stocks graded from the filings", rows=rows[:6], stats=stats, map_svg=quality_value_map(rows),
                               desc="Source-backed grades, valuations and project scorecards for gold, silver, copper, uranium and nickel mining companies.")

    @app.get("/companies")
    def companies():
        q, metal, stage = request.args.get("q", "").strip()[:60], request.args.get("metal", ""), request.args.get("stage", "")
        sort = request.args.get("sort", "name") if request.args.get("sort") in SORTS else "name"
        page = max(1, int(request.args.get("page", 1))) if request.args.get("page", "1").isdigit() else 1
        stmt = _latest_published()
        if q:
            stmt = stmt.where(Company.name.ilike(_like(q), escape="\\") | Company.ticker.ilike(_like(q), escape="\\"))
        if metal in METAL_NAMES:
            stmt = stmt.where(Company.primary_metals.like(f"%{metal}%"))
        if stage in STAGE_NAMES:
            stmt = stmt.where(Company.stage == stage)
        order = {"name": [Company.name], "quality": [Snapshot.quality_score.desc().nulls_last(), Company.name],
                 "value": [Snapshot.value_score.desc().nulls_last(), Company.name], "updated": [Snapshot.taken_at.desc()]}[sort]
        with db.session_scope(eng) as s:
            total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = [_row(c, sn) for c, sn in s.execute(stmt.order_by(*order).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE))]
            all_rows = [_row(c, sn) for c, sn in s.execute(stmt.limit(400))]
        pages = max(1, -(-total // PAGE_SIZE))
        return render_template("companies.html", title="Companies | HeadGrade", rows=rows, total=total, page=page, pages=pages, q=q, metal=metal, stage=stage,
                               sort=sort, sorts=SORTS, map_svg=quality_value_map(all_rows), desc="Browse graded mining companies by metal, stage and grade.",
                               args={k: v for k, v in (("q", q), ("metal", metal), ("stage", stage), ("sort", sort)) if v})

    @app.get("/company/<ticker>")
    def company(ticker):
        with db.session_scope(eng) as s:
            hit = s.execute(_latest_published().where(func.upper(Company.ticker) == ticker.upper())).first()
            if not hit:
                abort(404)
            co, snap = hit
            snaps = s.scalars(select(Snapshot).where(Snapshot.company_id == co.id, Snapshot.published.is_(True)).order_by(Snapshot.id.desc()).limit(14)).all()
            card = json.loads(snap.card_json)
            prev = json.loads(snaps[1].card_json) if len(snaps) > 1 else None
            changes = pipeline.diff_cards(prev, card) if prev else None
            history = [{"date": x.taken_at.date(), "price": x.price, "q": grading.letter(x.quality_score), "v": grading.letter(x.value_score),
                        "qs": x.quality_score, "vs": x.value_score} for x in snaps]
            summary = _summary(snap)
        body = render.card_body(card, changes)
        return render_template("company.html", title=f"{co.name} ({co.ticker}) grade | HeadGrade", body=body, history=history, summary=summary, co=co,
                               desc=f"{co.name} ({co.ticker}): quality {summary['quality_grade']}, valuation {summary['value_grade']}. {summary['quadrant']}. Every input sourced.")

    for slug, title, desc in (("methodology", "How HeadGrade grades mining companies", "Sources, verification, stage-specific methods and limits."),
                              ("about", "About HeadGrade", "Who runs HeadGrade and why it exists."),
                              ("disclosures", "Disclosures and terms", "Positions, sponsorship, data sources and the legal small print.")):
        app.add_url_rule(f"/{slug}", slug, (lambda sl, ti, de: lambda: render_template(f"{sl}.html", title=f"{ti} | HeadGrade", desc=de))(slug, title, desc))

    @app.get("/brand/<path:name>")
    def brand_file(name):
        return send_from_directory(BRAND_DIR, name, max_age=86400)

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(BRAND_DIR, "headgrade-app-icon.svg", mimetype="image/svg+xml")

    @app.get("/healthz")
    def healthz():
        with db.session_scope(eng) as s:
            s.scalar(select(func.count()).select_from(Company))
        return {"ok": True}

    @app.get("/robots.txt")
    def robots():
        return Response(f"User-agent: *\nAllow: /\nSitemap: {site_url or request.url_root.rstrip('/')}/sitemap.xml\n", mimetype="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        base = site_url or request.url_root.rstrip("/")
        with db.session_scope(eng) as s:
            tickers = [c.ticker for c, _ in s.execute(_latest_published())]
        urls = [f"{base}{p}" for p in ("/", "/companies", "/methodology", "/about", "/disclosures")] + [f"{base}/company/{t}" for t in tickers]
        xml = "<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls) + "</urlset>"
        return Response(xml, mimetype="application/xml")

    return app
