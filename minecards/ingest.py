"""Store a document, extract facts, verify quotes, normalise units, and save facts for review."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import units
from .db import Company, Document, Fact, Project
from .extract import ExtractedFact, extract_document, verify_fact
from .registry import METAL_UNIT, REGISTRY

STAGE_WORDS = [("preliminary economic", "PEA"), ("pea", "PEA"), ("pre-feasibility", "PFS"), ("prefeasibility", "PFS"), ("pfs", "PFS"),
               ("feasibility", "FS"), ("definitive", "FS"), ("bankable", "FS"), ("construction", "construction"), ("production", "production")]
PERMIT_WORDS = [("fully permitted", "fully_permitted"), ("all permits", "fully_permitted"), ("approved", "approved"), ("granted", "approved"),
                ("in process", "in_process"), ("under review", "in_process"), ("application", "in_process"), ("baseline", "baseline"), ("not started", "none")]


def _enum(text: str | None, words) -> str | None:
    t = (text or "").lower()
    return next((v for k, v in words if k in t), None)


@dataclass
class IngestSummary:
    document_id: int
    extracted: int
    saved: int
    auto_rejected: int
    flagged: int
    skipped_duplicates: int


def _project(session: Session, company: Company, name: str | None, notes: list[str]) -> Project | None:
    if not name:
        return None
    low = name.strip().lower()
    for p in company.projects:
        if p.name.lower() == low or low in p.name.lower() or p.name.lower() in low:
            return p
    p = Project(company_id=company.id, name=name.strip())
    session.add(p)
    session.flush()
    session.refresh(company)
    notes.append(f"new project created from extraction: '{name}'")
    return p


def to_row(ef: ExtractedFact, company: Company, project: Project | None, doc: Document, page: int | None,
           auto_check: str) -> tuple[Fact, list[str]]:
    spec = REGISTRY[ef.key]
    notes: list[str] = []
    value_num, unit, ccy, text = None, None, None, ef.text
    if spec.family in ("text", "date"):
        text = ef.text or (str(ef.value) if ef.value is not None else None)
        if ef.key == "study_stage":
            norm = _enum(text, STAGE_WORDS)
            notes.append("study stage normalised" if norm else "study stage not recognised")
            text = norm or text
        if ef.key == "permit_stage":
            norm = _enum(text, PERMIT_WORDS)
            notes.append("permit stage normalised" if norm else "permit stage not recognised")
            text = norm or text
    elif ef.value is not None:
        raw_unit = (ef.unit or "").strip()
        if spec.family == "price":
            _, _, ccy = units.parse_unit(raw_unit)
            value_num, unit = ef.value, METAL_UNIT.get(ef.metal or "", None)
            low = raw_unit.lower()
            if ef.metal and "/" in low and METAL_UNIT[ef.metal] not in low:
                notes.append(f"price unit '{raw_unit}' does not look per {METAL_UNIT[ef.metal]}")
        elif spec.family == "pct":
            value_num, unit = ef.value, "%"
            if raw_unit and "%" not in raw_unit and ef.value <= 1.0:
                notes.append("percentage may be stated as a fraction: check")
        elif spec.family == "grade":
            value_num, unit = ef.value, (units.parse_unit(raw_unit)[1] or raw_unit)
        else:
            value_num, base, ccy = units.normalize(ef.value, raw_unit)
            unit = base
            if spec.family == "metal_qty" and ef.metal:
                want = METAL_UNIT[ef.metal]
                if base != want:
                    notes.append(f"unit '{raw_unit}' is not {want}: value may be wrong")
            if spec.family == "currency" and ccy is None:
                notes.append("currency not stated in the unit: check")
    as_of = None
    if ef.as_of:
        try:
            as_of = date.fromisoformat(ef.as_of[:10])
        except ValueError:
            notes.append(f"as_of '{ef.as_of}' not an ISO date")
    status, conf = "unverified", 0.8
    if auto_check == "quote_not_found":
        status, conf = "rejected", 0.0
        notes.append("rejected automatically: the quote could not be found on the cited page")
    elif auto_check == "number_not_in_quote":
        conf = 0.3
        notes.append("the stated number does not appear in the quote")
    if any(("not" in n and "recognised" in n) or "may be wrong" in n or "check" in n for n in notes):
        conf = min(conf, 0.5)
    row = Fact(company_id=company.id, project_id=project.id if project else None, key=ef.key, metal=ef.metal, category=ef.category,
               value_num=value_num, value_text=text, unit=unit, currency=ccy, as_of=as_of or doc.effective_date, doc_id=doc.id,
               page=page, quote=ef.quote[:600], as_written=(f"{ef.value:g} {ef.unit or ''}".strip() if ef.value is not None else None),
               confidence=conf, status=status, auto_check=auto_check, note="; ".join(notes) or None)
    return row, notes


def ingest_pages(session: Session, company: Company, pages: list[str], kind: str, title: str, client,
                 effective_date: date | None = None, url: str | None = None, model: str | None = None,
                 sha256: str | None = None) -> IngestSummary:
    sha = sha256 or hashlib.sha256("\n".join(pages).encode()).hexdigest()
    dup = session.scalar(select(Document).where(Document.company_id == company.id, Document.sha256 == sha))
    if dup:
        return IngestSummary(dup.id, 0, 0, 0, 0, 0)
    doc = Document(company_id=company.id, kind=kind, title=title, url=url, sha256=sha, effective_date=effective_date,
                   pages_json=json.dumps(pages))
    session.add(doc)
    session.flush()
    extracted = extract_document(client, company.name, kind, pages, model)
    saved = rejected = flagged = dups = 0
    seen: set[tuple] = set()
    for ef in extracted:
        auto, page = verify_fact(ef, pages)
        notes: list[str] = []
        proj = _project(session, company, ef.project, notes) if REGISTRY[ef.key].scope == "project" else None
        row, _ = to_row(ef, company, proj, doc, page or ef.page, auto)
        if notes:
            row.note = "; ".join(filter(None, [row.note, *notes]))
        sig = (row.key, row.metal, row.category, row.project_id, row.value_num, row.value_text, row.as_of)
        if sig in seen:
            dups += 1
            continue
        seen.add(sig)
        session.add(row)
        saved += 1
        rejected += row.status == "rejected"
        flagged += row.auto_check == "number_not_in_quote" or bool(row.note and row.status != "rejected")
    session.flush()
    return IngestSummary(doc.id, len(extracted), saved, rejected, flagged, dups)


def ingest_pdf(session: Session, company: Company, path: str, kind: str, client, effective_date: date | None = None,
               title: str | None = None, url: str | None = None, model: str | None = None) -> IngestSummary:
    from .extract import load_pdf_pages
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    return ingest_pages(session, company, load_pdf_pages(path), kind, title or path.rsplit("/", 1)[-1], client,
                        effective_date, url, model, sha)
