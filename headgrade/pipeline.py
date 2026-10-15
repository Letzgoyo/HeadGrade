"""Database -> facts -> valuation -> grade -> card, plus weekly snapshots and diffs."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import checklist, grading, valuation as V
from .db import Benchmark, Company, Document, Fact, JurisdictionScore, PriceDeck, Project, Snapshot
from .decks import Deck
from .facts import F, FactBook
from .registry import METALS


def load_factbook(session: Session, company: Company, verified_only: bool = False, human_only: bool = False) -> FactBook:
    rows = session.scalars(select(Fact).where(Fact.company_id == company.id)).all()
    if human_only:  # ignore anything a machine verified: only a person's sign-off counts
        rows = [r for r in rows if not (r.status == "verified" and r.verified_by == "auto")]
    facts = [F(r.key, r.value_num, r.value_text, r.unit, r.currency, r.metal, r.category, r.project_id, r.as_of, r.status,
               r.confidence, r.id, r.doc_id, r.page, r.quote, r.id, r.verified_by) for r in rows]
    return FactBook(facts, include_unverified=not verified_only)


def latest_deck(session: Session, name: str | None = None) -> Deck | None:
    q = select(PriceDeck).order_by(PriceDeck.as_of.desc(), PriceDeck.id.desc())
    if name:
        q = q.where(PriceDeck.name == name)
    row = session.scalars(q).first()
    return Deck.from_row(row) if row else None


def jurisdiction_table(session: Session) -> dict[str, float]:
    return {r.key: r.score for r in session.scalars(select(JurisdictionScore))}


def build_card(session: Session, company: Company, deck: Deck, market: V.Market, publish: bool = False,
               today: date | None = None, human_only: bool = False) -> dict:
    """publish=True uses ONLY facts a human has verified (or overridden). Draft mode includes unverified facts and says so."""
    today = today or date.today()
    fb = load_factbook(session, company, verified_only=publish, human_only=human_only)
    projects = [V.Proj(p.id, p.name, p.primary_metal) for p in company.projects]
    primary = (company.primary_metals.split(",")[0].strip() or "au") if company.primary_metals else "au"
    benchmarks = list(session.scalars(select(Benchmark)))
    stage = company.stage
    if stage == "developer":
        val = V.value_developer(fb, market, projects, deck)
    elif stage == "explorer":
        val = V.value_explorer(fb, market, projects, deck, benchmarks, primary)
    elif stage == "producer":
        val = V.value_producer(fb, market, projects, deck, primary)
    elif stage == "royalty":
        val = V.value_royalty(fb, market, deck, benchmarks, primary)
    else:
        raise ValueError(f"unknown stage '{stage}'")
    g = grading.grade(fb, stage, projects, val, jurisdiction_table(session), today)
    table = jurisdiction_table(session)
    cl = checklist.evaluate(fb, stage, projects, val, table, primary)
    # re-read confidence now that grading has also consumed facts
    val["confidence"] = fb.confidence()
    ids = {u.fact_id for u in fb.used if u.fact_id}
    rows = {r.id: r for r in session.scalars(select(Fact).where(Fact.id.in_(ids)))} if ids else {}
    docs = {d.id: d for d in session.scalars(select(Document).where(Document.company_id == company.id))}
    evidence, seen = [], set()
    for u in fb.used:
        if u.fact_id in seen or u.fact_id not in rows:
            continue
        seen.add(u.fact_id)
        r = rows[u.fact_id]
        d = docs.get(r.doc_id)
        evidence.append({"label": u.label, "value": r.as_written or (r.value_text or r.value_num), "status": r.status, "verified_by": r.verified_by, "unit": r.unit, "currency": r.currency,
                         "value_raw": r.value_num if r.value_num is not None else r.value_text,
                         "document": d.title if d else None, "document_date": str(d.effective_date) if d and d.effective_date else None,
                         "page": r.page, "quote": r.quote, "url": d.url if d else None})
    for sq in cl["squares"]:
        for e in sq["evidence"]:
            d = docs.get(e.pop("doc_id", None))
            e["document"] = d.title if d else None
    return {
        "mode": "published" if publish else "draft",
        "company": {"name": company.name, "ticker": company.ticker, "exchange": company.exchange, "stage": stage,
                    "metals": [m for m in company.primary_metals.split(",") if m], "jurisdiction": company.jurisdiction,
                    "projects": [p.name for p in company.projects]},
        "market": {"price": market.price, "currency": market.currency, "price_date": str(market.price_date) if market.price_date else None},
        "deck": {"name": deck.name, "as_of": str(deck.as_of) if deck.as_of else None, "prices": deck.prices},
        "grade": g, "valuation": val, "checklist": cl, "evidence": evidence,
        "disclosures": {"sponsored": company.sponsored, "owner_holds_position": company.held_by_owner},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _jsonable(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"not serialisable: {type(o)}")


def card_json(card: dict) -> str:
    return json.dumps(card, default=_jsonable, indent=1)


def save_snapshot(session: Session, company: Company, card: dict) -> Snapshot:
    s = Snapshot(company_id=company.id, price=card["market"]["price"], card_json=card_json(card),
                 price_date=date.fromisoformat(card["market"]["price_date"]) if card["market"]["price_date"] else None,
                 quality_score=card["grade"]["quality_score"], value_score=card["grade"]["value_score"])
    session.add(s)
    session.flush()
    return s


def diff_cards(old: dict | None, new: dict) -> list[str]:
    """Plain-English 'what changed since last week'."""
    if not old:
        return ["First card for this company."]
    out = []
    og, ng = old["grade"], new["grade"]
    for label, key in (("Quality grade", "quality_grade"), ("Valuation grade", "value_grade")):
        if og[key] != ng[key]:
            out.append(f"{label} moved from {og[key]} to {ng[key]}.")
    for cat, v in ng["categories"].items():
        a, b = og["categories"].get(cat, {}).get("score"), v["score"]
        if a is not None and b is not None and abs(a - b) >= 10:
            out.append(f"{cat.capitalize()} score {'rose' if b > a else 'fell'} from {a:.0f} to {b:.0f}.")
    ot, nt = {f["text"] for f in og["red_flags"]}, {f["text"] for f in ng["red_flags"]}
    out += [f"New red flag: {t}" for t in sorted(nt - ot)]
    out += [f"Red flag cleared: {t}" for t in sorted(ot - nt)]
    if old["deck"]["prices"] != new["deck"]["prices"]:
        out.append("The metal price deck was updated.")
    if old["market"]["price"] and new["market"]["price"]:
        chg = 100 * (new["market"]["price"] / old["market"]["price"] - 1)
        if abs(chg) >= 10:
            out.append(f"Share price moved {chg:+.0f}% since the last card.")
    oc, nc = (old.get("checklist") or {}).get("counts") or {}, (new.get("checklist") or {}).get("counts") or {}
    if oc and nc and oc.get("yes") != nc.get("yes"):
        out.append(f"Project checklist went from {oc.get('yes')} to {nc.get('yes')} squares ticked.")
    ov, nv = old["valuation"]["metrics"].get("p_nav"), new["valuation"]["metrics"].get("p_nav")
    if ov and nv and abs(nv / ov - 1) >= 0.15:
        out.append(f"Price-to-NAV moved from {ov:.2f} to {nv:.2f}.")
    return out or ["No material changes."]


def last_snapshot(session: Session, company: Company) -> dict | None:
    s = session.scalars(select(Snapshot).where(Snapshot.company_id == company.id).order_by(Snapshot.taken_at.desc())).first()
    return json.loads(s.card_json) if s else None
