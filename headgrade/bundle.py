"""Import facts that were read OUTSIDE the weekly job (for example by a person using Claude in a chat, with no API key).

A bundle is one JSON file: the company, the document pages the facts cite, and one or two independent readings. Nothing is trusted
on arrival: every quote is re-checked against the page text in the bundle, then the usual auto-verification runs (second reading,
ranges, arithmetic), and published cards still use verified facts only."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import autoverify, ingest, pipeline, prices, stages
from .db import Company, PriceDeck
from .extract import ExtractedFact


def _facts(readings: list[dict], doc_id: str) -> list[ExtractedFact]:
    return [ExtractedFact(**{k: v for k, v in f.items() if k != "doc"}) for f in readings if f.get("doc") == doc_id]


def _pages(doc: dict) -> list[str]:
    n = int(doc.get("n_pages") or max((int(k) for k in doc["pages"]), default=0))
    return [doc["pages"].get(str(i), "") for i in range(1, n + 1)]


def import_bundle(session: Session, data: dict, build: bool = True, today: date | None = None) -> dict:
    c = data["company"]
    co = session.scalar(select(Company).where(Company.ticker == c["ticker"].upper()))
    if co is None:
        co = Company(name=c["name"], ticker=c["ticker"].upper(), exchange=c.get("exchange"), stage=c.get("stage", "explorer"),
                     stage_auto="stage" not in c, currency=c.get("currency", "USD"), primary_metals=c.get("primary_metals", ""),
                     jurisdiction=c.get("jurisdiction"), held_by_owner=bool(c.get("held_by_owner")), sponsored=bool(c.get("sponsored")))
        session.add(co)
        session.flush()
    a, b = data["readings"]["a"], data["readings"].get("b")
    out = {"company": co.ticker, "documents": 0, "facts": 0, "rejected": 0, "auto_verified": 0, "needs_review": 0, "unconfirmed": 0}
    for d in data["documents"]:
        eff = date.fromisoformat(d["effective_date"]) if d.get("effective_date") else None
        s = ingest.ingest_pages(session, co, _pages(d), d["kind"], d["title"], None, eff, d.get("url"), d.get("model"), extracted=_facts(a, d["id"]))
        out["documents"] += 1
        out["facts"] += s.saved
        out["rejected"] += s.auto_rejected
        if s.saved:
            st = autoverify.apply(session, co, s.document_id, _facts(b, d["id"]) if b is not None else None)
            out["auto_verified"] += st.auto_verified
            out["needs_review"] += st.needs_review
            out["unconfirmed"] += st.unconfirmed
    extra = autoverify.reconsider(session, co)
    out["auto_verified"] += extra.auto_verified
    out["needs_review"] += extra.needs_review
    stages.apply(session, co, pipeline.load_factbook(session, co))
    out["stage"] = co.stage
    if data.get("price"):
        p = data["price"]
        prices.set_price(session, co.ticker, p["price"], p["currency"], date.fromisoformat(p["as_of"]), "bundle")
    if data.get("deck"):
        session.add(PriceDeck(name=data["deck"].get("name", "spot"), as_of=date.fromisoformat(data["deck"]["as_of"]), prices_json=json.dumps(data["deck"]["prices"])))
    session.flush()
    if build:
        from .weekly import build_company_card
        card, status, _ = build_company_card(session, co, today, publish=True)
        out["card"] = status
        if card:
            out["quality"], out["valuation"] = card["grade"]["quality_grade"], card["grade"]["value_grade"]
    return out


def build_bundle(company: dict, docs: list[dict], reading_a: list[dict], reading_b: list[dict] | None = None, price=None, deck=None) -> dict:
    """docs: [{id,title,kind,url,effective_date,pages:[str,...]}]. Keeps only the pages a reading cites (plus neighbours) so the file stays small."""
    keep: dict[str, set[int]] = {d["id"]: set() for d in docs}
    for f in list(reading_a) + list(reading_b or []):
        for n in (f["page"] - 1, f["page"], f["page"] + 1):
            keep.setdefault(f["doc"], set()).add(n)
    out_docs = []
    for d in docs:
        n = len(d["pages"])
        out_docs.append({**{k: v for k, v in d.items() if k != "pages"}, "n_pages": n,
                         "pages": {str(i): d["pages"][i - 1] for i in sorted(keep[d["id"]]) if 1 <= i <= n}})
    return {"company": company, "documents": out_docs, "readings": {"a": reading_a, **({"b": reading_b} if reading_b is not None else {})},
            "price": price, "deck": deck}
