"""The weekly job: for each company, find new filings, read each document twice, auto-verify, infer the stage,
rebuild the card, and record what changed. Every step is idempotent and one company's failure never stops the rest."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import autoverify, config, ingest, pipeline, prices, stages, valuation as V
from .db import Company, Filing, JobRun
from .edgar import Edgar, fetch_filing_documents
from .extract import extract_document


@dataclass
class CompanyRun:
    ticker: str
    filings_new: int = 0
    filings_error: int = 0
    docs: int = 0
    facts: int = 0
    auto_verified: int = 0
    needs_review: int = 0
    errors: list = field(default_factory=list)
    stage: str | None = None
    card_status: str = ""
    quality: str = ""
    valuation: str = ""
    changes: list = field(default_factory=list)
    preview: list = field(default_factory=list)     # dry run: what would be read
    stopped_by_cap: bool = False

    def as_dict(self):
        return self.__dict__.copy()


def build_company_card(session: Session, company: Company, today: date | None = None, publish: bool = True,
                       human_only: bool = False, save: bool = True) -> tuple[dict | None, str, list]:
    price = prices.latest_price(session, company.ticker)
    if price is None:
        return None, f"no price for {company.ticker}: set one with price-set", []
    deck = pipeline.latest_deck(session)
    if deck is None:
        return None, "no metal price deck: set one with deck-set", []
    mk = V.Market(price.price, price.currency, price.as_of, prices.latest_fx(session))
    card = pipeline.build_card(session, company, deck, mk, publish=publish, today=today, human_only=human_only)
    changes = pipeline.diff_cards(pipeline.last_snapshot(session, company), card)
    if save:
        pipeline.save_snapshot(session, company, card)
    return card, "ok", changes


def process_company(session: Session, company: Company, edgar: Edgar, reader_a, reader_b=None, *, today: date | None = None,
                    since_days: int = 400, max_docs_per_filing: int = 8, model_a: str | None = None, model_b: str | None = None,
                    build: bool = True, human_only: bool = False, dry_run: bool = False, budget: dict | None = None) -> CompanyRun:
    """dry_run: download and classify only (free); no Claude calls, nothing saved.
    budget: shared {"docs_left": n}; once spent we stop before the next filing and leave it unrecorded so the next run continues."""
    today = today or date.today()
    run = CompanyRun(company.ticker)
    cik = company.cik or edgar.cik_for_ticker(company.ticker)
    if not cik:
        run.errors.append("no SEC CIK for this ticker: set it with company-set-cik")
        return run
    if not dry_run:
        company.cik = cik
    since = (company.last_filing_seen - timedelta(days=1)) if company.last_filing_seen else today - timedelta(days=since_days)
    refs = edgar.recent_filings(cik, since=since)
    known = {a for a in session.scalars(select(Filing.accession).where(Filing.company_id == company.id))}
    for ref in refs:
        if ref.accession in known:
            continue
        if budget is not None and budget["docs_left"] <= 0 and not dry_run:
            run.stopped_by_cap = True
            break
        run.filings_new += 1
        if dry_run:
            try:
                docs = fetch_filing_documents(edgar, cik, ref, max_docs_per_filing)
                run.docs += len(docs)
                run.preview.append(f"{ref.form} {ref.filed}: " + (", ".join(f"{d.name} [{d.kind}, {len(d.pages)} pages]" for d in docs) or "no readable documents"))
            except Exception as exc:
                run.filings_error += 1
                run.errors.append(f"{ref.accession}: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        try:
            with session.begin_nested():   # a failure rolls back only this filing
                docs = fetch_filing_documents(edgar, cik, ref, max_docs_per_filing)
                if budget is not None:
                    docs = docs[:max(budget["docs_left"], 0)]
                    budget["docs_left"] -= len(docs)
                n_facts = 0
                for d in docs:
                    s = ingest.ingest_pages(session, company, d.pages, d.kind, f"{ref.form} {ref.filed} {d.name}", reader_a,
                                            ref.period or ref.filed, d.url, model_a)
                    run.docs += 1
                    n_facts += s.saved
                    if s.saved:
                        second = extract_document(reader_b, company.name, d.kind, d.pages, model_b) if reader_b is not None else None
                        st = autoverify.apply(session, company, s.document_id, second)
                        run.auto_verified += st.auto_verified
                        run.needs_review += st.needs_review
                run.facts += n_facts
                session.add(Filing(company_id=company.id, accession=ref.accession, form=ref.form, filed=ref.filed, period=ref.period,
                                   url=edgar.doc_url(cik, ref.accession, ref.primary_doc), status="processed" if docs else "skipped",
                                   n_docs=len(docs), n_facts=n_facts, processed_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        except Exception as exc:
            run.filings_error += 1
            run.errors.append(f"{ref.accession}: {type(exc).__name__}: {str(exc)[:200]}")
            session.add(Filing(company_id=company.id, accession=ref.accession, form=ref.form, filed=ref.filed, period=ref.period,
                               status="error", error=f"{type(exc).__name__}: {str(exc)[:400]}"))
        if ref.filed and (company.last_filing_seen is None or ref.filed > company.last_filing_seen):
            company.last_filing_seen = ref.filed
    if dry_run:
        return run
    session.flush()
    extra = autoverify.reconsider(session, company)
    run.auto_verified += extra.auto_verified
    run.needs_review += extra.needs_review
    info = stages.apply(session, company, pipeline.load_factbook(session, company))
    run.stage = company.stage
    if build:
        card, status, changes = build_company_card(session, company, today, publish=True, human_only=human_only)
        run.card_status, run.changes = status, changes
        if card:
            run.quality, run.valuation = card["grade"]["quality_grade"], card["grade"]["value_grade"]
    return run


def run_weekly(engine, edgar: Edgar, reader_a, reader_b=None, tickers: list[str] | None = None, max_total_docs: int | None = None,
               **kw) -> dict:
    """One session (transaction) per company so a failure in one cannot affect the others."""
    from .db import session_scope
    with session_scope(engine) as s:
        job = JobRun(kind="weekly")
        s.add(job)
        s.flush()
        job_id = job.id
        wanted = [t.upper() for t in tickers] if tickers else None
        cids = [c.id for c in s.scalars(select(Company).order_by(Company.ticker)) if not wanted or c.ticker in wanted]
    results = []
    kw["budget"] = {"docs_left": max_total_docs} if max_total_docs is not None else None
    for cid in cids:
        try:
            with session_scope(engine) as s:
                results.append(process_company(s, s.get(Company, cid), edgar, reader_a, reader_b, **kw).as_dict())
        except Exception as exc:
            results.append({"ticker": str(cid), "errors": [f"{type(exc).__name__}: {str(exc)[:200]}"]})
    summary = {"companies": len(results), "docs": sum(r.get("docs", 0) for r in results),
               "auto_verified": sum(r.get("auto_verified", 0) for r in results), "needs_review": sum(r.get("needs_review", 0) for r in results),
               "errors": sum(len(r.get("errors", [])) for r in results), "results": results}
    if kw.get("dry_run"):
        return summary
    with session_scope(engine) as s:
        job = s.get(JobRun, job_id)
        job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        job.summary_json = json.dumps(summary, default=str)
    return summary
