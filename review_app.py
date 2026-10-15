"""Private reviewer/admin screen. Run:  streamlit run review_app.py
(The public site is separate and read-only; this is where you verify facts and manage data.)"""
from __future__ import annotations

import json
import os
from datetime import date

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select

from headgrade import config, db, pipeline, review
from headgrade.db import Benchmark, Company, Fact, JurisdictionScore, PriceDeck, Project
from headgrade.registry import METALS, METAL_UNIT, STAGES
from headgrade.render import render_html
from headgrade.valuation import Market

st.set_page_config(page_title="HeadGrade review", page_icon=str(Path(__file__).parent / "brand" / "headgrade-app-icon.png"), layout="wide")
engine = db.make_engine()
db.init_db(engine)

PAGES = ["Review queue", "Pipeline status", "Card preview", "Companies & documents", "Decks & benchmarks"]
_brand = Path(__file__).parent / "brand"
st.logo(str(_brand / "headgrade-1-core.svg"), icon_image=str(_brand / "headgrade-icon.svg"))
page = st.sidebar.radio("View", PAGES)
st.sidebar.caption("Private admin screen. Draft cards use unverified facts; published cards use only what you verify.")


def show(f) -> str:
    """A readable value: $410.0M, 1,332,417,631, 4.5, PFS."""
    if f.value_text is not None:
        return f.value_text
    if f.value_num is None:
        return "-"
    v, a = f.value_num, abs(f.value_num)
    body = f"{v / 1e9:,.3f}B" if a >= 1e9 else f"{v / 1e6:,.1f}M" if a >= 1e6 else f"{v:,.6g}"
    return (f"{f.currency} " if f.currency else "") + body


def companies(s):
    return list(s.scalars(select(Company).order_by(Company.name)))


def pick_company(s, key="co"):
    cos = companies(s)
    if not cos:
        st.info("No companies yet. Add one under **Companies & documents**.")
        return None
    by_id = {c.id: c for c in cos}   # select by id and re-load: ORM objects must not outlive their session
    cid = st.selectbox("Company", list(by_id), format_func=lambda i: f"{by_id[i].name} ({by_id[i].ticker}, {by_id[i].stage})", key=key)
    return by_id[cid]


# ------------------------------------------------------------------ review queue
def page_review():
    st.header("Review queue")
    with db.session_scope(engine) as s:
        co = pick_company(s)
        if not co:
            return
        p = review.progress(s, co.id)
        st.progress(((p["verified"] + p["override"]) / p["total"]) if p["total"] else 0.0,
                   text=f"{p['verified'] + p['override']} of {p['total']} facts verified; {p['critical_unreviewed']} critical inputs still to review; {p['rejected']} rejected")
        c1, c2, c3 = st.columns(3)
        status = c1.selectbox("Show", ["unverified", "verified", "override", "rejected", "all"])
        attention = c2.checkbox("Only what needs me (conflicts, failed checks, audits)", value=True)
        crit = c2.checkbox("Critical inputs only", value=not attention)
        key_q = c3.text_input("Filter by key", "")
        audits = review.audit_queue(s, co.id)
        if audits:
            st.subheader(f"Spot-checks ({len(audits)}): is the machine's value right?")
            for f in audits[:10]:
                a1, a2, a3 = st.columns([4, 1, 1])
                a1.markdown(f"**{f.key}** {f.metal or ''} = `{show(f)}` {f.unit or ''} (p.{f.page})  \n_{f.quote}_")
                if a2.button("Right", key=f"ap{f.id}"):
                    with db.session_scope(engine) as s2:
                        review.record_audit(s2, f.id, True)
                    st.rerun()
                if a3.button("Wrong", key=f"af{f.id}"):
                    with db.session_scope(engine) as s2:
                        review.record_audit(s2, f.id, False)
                    st.rerun()
        rows = review.queue(s, co.id, status, crit, key_q, limit=40, needs_attention=attention and status == "unverified")
        if not rows:
            st.success("Nothing here.")
        for f in rows:
            flag = {"quote_found": "✅", "number_not_in_quote": "⚠️", "quote_not_found": "❌"}.get(f.auto_check or "", "")
            title = f"{flag} #{f.id}  {f.key}  {f.metal or ''} {f.category or ''}  =  {show(f)} {f.unit or ''}   (p.{f.page})"
            with st.expander(title, expanded=False):
                st.markdown(f"**Quote from the document:** _{f.quote}_")
                if f.as_written:
                    st.caption(f"As written: {f.as_written}  |  automatic check: {f.auto_check}  |  confidence {f.confidence:.1f}")
                if f.note:
                    st.warning(f.note)
                for c in json.loads(f.checks or "[]"):
                    st.caption(("✅ " if c["passed"] else "❌ ") + c["name"].replace("_", " ") + (f": {c['detail']}" if c["detail"] else ""))
                ctx = review.page_text(s, f, context=0)
                if ctx and st.toggle("Show the page text", key=f"pg{f.id}"):
                    st.text_area("Page", ctx, height=260, key=f"pgt{f.id}", label_visibility="collapsed")
                b1, b2, b3 = st.columns([1, 1, 3])
                if b1.button("Verify", key=f"v{f.id}", type="primary"):
                    with db.session_scope(engine) as s2:
                        review.set_status(s2, f.id, "verified")
                    st.rerun()
                if b2.button("Reject", key=f"r{f.id}"):
                    with db.session_scope(engine) as s2:
                        review.set_status(s2, f.id, "rejected")
                    st.rerun()
                with b3:
                    nv = st.text_input("Correct the value (number or text)", key=f"nv{f.id}", placeholder="e.g. 1.63e6, or PFS")
                    if st.button("Save correction (counts as verified)", key=f"o{f.id}") and nv.strip():
                        with db.session_scope(engine) as s2:
                            try:
                                review.override(s2, f.id, value=float(nv.replace(",", "")))
                            except ValueError:
                                review.override(s2, f.id, text=nv.strip())
                        st.rerun()


# ------------------------------------------------------------------ pipeline status
def page_pipeline():
    from sqlalchemy import func
    from headgrade.db import Filing, JobRun
    st.header("Pipeline status")
    with db.session_scope(engine) as s:
        rows = s.execute(select(Fact.status, Fact.verified_by, func.count()).group_by(Fact.status, Fact.verified_by)).all()
        st.subheader("Facts")
        st.table([{"status": r[0], "verified by": r[1] or "-", "count": r[2]} for r in rows])
        a = review.audit_stats(s)
        st.subheader("Spot-check accuracy of automatic verification")
        st.write(f"{a['audited']} audited, {a['failed']} wrong, {a['pending']} waiting. " +
                 (f"Error rate {100 * a['error_rate']:.1f}%." if a["error_rate"] is not None else "No audits finished yet."))
        st.subheader("Recent weekly runs")
        for j in s.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(5)):
            d = json.loads(j.summary_json or "{}")
            st.write(f"{j.started_at:%Y-%m-%d %H:%M}: {d.get('companies', '?')} companies, {d.get('docs', '?')} documents, "
                     f"{d.get('auto_verified', '?')} auto-verified, {d.get('needs_review', '?')} need review, {d.get('errors', '?')} errors")
        errs = list(s.scalars(select(Filing).where(Filing.status == "error").order_by(Filing.id.desc()).limit(20)))
        if errs:
            st.subheader("Filings that failed")
            st.table([{"company": f.company_id, "accession": f.accession, "form": f.form, "error": (f.error or "")[:120]} for f in errs])


# ------------------------------------------------------------------ card preview
def page_card():
    st.header("Card preview")
    with db.session_scope(engine) as s:
        co = pick_company(s, "co2")
        if not co:
            return
        deck = pipeline.latest_deck(s)
        if not deck:
            st.warning("Set a metal price deck first (Decks & benchmarks).")
            return
        c1, c2, c3, c4 = st.columns(4)
        price = c1.number_input("Share price", min_value=0.0, value=1.0, step=0.01, format="%.3f")
        ccy = c2.selectbox("Trading currency", ["USD", "CAD", "AUD", "GBP"], index=["USD", "CAD", "AUD", "GBP"].index(co.currency) if co.currency in ["USD", "CAD", "AUD", "GBP"] else 0)
        fx_cad = c3.number_input("USD per CAD", value=0.72, step=0.01, format="%.4f")
        fx_aud = c4.number_input("USD per AUD", value=0.65, step=0.01, format="%.4f")
        mode = st.radio("Mode", ["Draft (includes unverified facts)", "Publish (verified facts only)"], horizontal=True)
        mk = Market(price, ccy, date.today(), {"USD": 1.0, "CAD": fx_cad, "AUD": fx_aud, "GBP": 1.25})
        card = pipeline.build_card(s, co, deck, mk, publish=mode.startswith("Publish"))
        changes = pipeline.diff_cards(pipeline.last_snapshot(s, co), card)
        components.html(render_html(card, changes), height=900, scrolling=True)
        if st.button("Save this as the weekly snapshot"):
            pipeline.save_snapshot(s, co, card)
            st.success("Saved.")


# ------------------------------------------------------------------ companies & documents
def page_companies():
    st.header("Companies & documents")
    with db.session_scope(engine) as s:
        with st.expander("Add a company"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Name"); ticker = c2.text_input("Ticker (Yahoo style, e.g. DML.TO)")
            stage = c3.selectbox("Stage", STAGES)
            c4, c5, c6 = st.columns(3)
            metals = c4.multiselect("Primary metals", METALS, default=["au"])
            jur = c5.text_input("Main jurisdiction"); ccy = c6.selectbox("Trading currency", ["USD", "CAD", "AUD", "GBP"])
            held = st.checkbox("I hold this stock (disclosed on the card)"); spons = st.checkbox("Company sponsors the site (disclosed on the card)")
            if st.button("Add company") and name and ticker:
                s.add(Company(name=name, ticker=ticker.upper(), stage=stage, primary_metals=",".join(metals), jurisdiction=jur,
                              currency=ccy, held_by_owner=held, sponsored=spons))
                st.success("Added.")
                st.rerun()
        co = pick_company(s, "co3")
        if not co:
            return
        st.write(f"**{co.name}**  |  stage: {co.stage}  |  metals: {co.primary_metals}  |  held: {co.held_by_owner}  |  sponsor: {co.sponsored}")
        pn = st.text_input("Add a project (name)")
        if st.button("Add project") and pn:
            s.add(Project(company_id=co.id, name=pn, primary_metal=(co.primary_metals.split(",")[0] or None)))
            st.rerun()
        st.write("Projects: " + (", ".join(p.name for p in co.projects) or "none"))
        st.subheader("Add a document")
        if not os.getenv("ANTHROPIC_API_KEY"):
            st.info("Set ANTHROPIC_API_KEY to read documents automatically.")
        kind = st.selectbox("Type", ["technical_report", "financials", "mdna", "presentation", "press_release"])
        eff = st.date_input("Effective date of the document", value=date.today())
        up = st.file_uploader("PDF", type=["pdf"])
        if up and st.button("Extract facts", disabled=not os.getenv("ANTHROPIC_API_KEY"), type="primary"):
            import anthropic
            from headgrade.ingest import ingest_pdf
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            (config.DATA_DIR / "docs").mkdir(exist_ok=True)
            path = config.DATA_DIR / "docs" / up.name
            path.write_bytes(up.getvalue())
            with st.spinner("Reading the document and checking every quote..."):
                try:
                    r = ingest_pdf(s, co, str(path), kind, anthropic.Anthropic(), eff, up.name)
                    st.success(f"Extracted {r.extracted}; saved {r.saved}; auto-rejected {r.auto_rejected} (quote not found); flagged {r.flagged}; duplicates skipped {r.skipped_duplicates}. Now review them.")
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------ decks & benchmarks
def page_decks():
    st.header("Metal price decks, jurisdictions and benchmarks")
    with db.session_scope(engine) as s:
        deck = pipeline.latest_deck(s)
        st.subheader("Metal price deck")
        st.caption("$/oz for au, ag, pt, pd; $/lb for cu, ni, u3o8, zn, pb. There are no built-in prices: set them from a source you trust.")
        cols = st.columns(5)
        new = {}
        for i, m in enumerate(["au", "ag", "cu", "ni", "u3o8"]):
            new[m] = cols[i].number_input(f"{m.upper()} $/{METAL_UNIT[m]}", min_value=0.0, value=float((deck.prices.get(m) if deck else 0.0) or 0.0), step=1.0 if m in ("au", "ag") else 0.05)
        if st.button("Save deck as today's") and any(v > 0 for v in new.values()):
            s.add(PriceDeck(name="spot", as_of=date.today(), prices_json=json.dumps({k: v for k, v in new.items() if v > 0})))
            st.success("Saved.")
            st.rerun()
        if deck:
            st.write(f"Current deck: {deck.describe()} (as of {deck.as_of})")
        st.subheader("Jurisdiction scores (0-100)")
        j1, j2, j3 = st.columns(3)
        jn = j1.text_input("Jurisdiction name (as it appears in filings)"); js = j2.number_input("Score", 0.0, 100.0, 70.0); jsrc = j3.text_input("Source", "Fraser Institute IAI")
        if st.button("Save jurisdiction") and jn:
            row = s.scalar(select(JurisdictionScore).where(JurisdictionScore.name == jn))
            if row:
                row.score, row.source = js, jsrc
            else:
                s.add(JurisdictionScore(name=jn, score=js, source=jsrc))
            st.rerun()
        st.table([{"name": r.name, "score": r.score, "source": r.source} for r in s.scalars(select(JurisdictionScore))])
        st.subheader("Peer benchmarks (needed to rate explorers and royalty companies)")
        st.table([{"kind": b.kind, "metal": b.metal, "stage": b.stage, "p25": b.p25, "median": b.median, "p75": b.p75, "n": b.n, "as_of": b.as_of, "source": b.source}
                  for b in s.scalars(select(Benchmark))])
        st.caption("Add benchmarks with: python -m headgrade.cli benchmark-add --kind peer_ev_per_unit --metal au --stage explorer --p25 .. --median .. --p75 .. --n .. --source ..")


{"Review queue": page_review, "Pipeline status": page_pipeline, "Card preview": page_card, "Companies & documents": page_companies, "Decks & benchmarks": page_decks}[page]()
