from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from . import config
from .jurisdictions import resolve_key as _jkey


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    ticker: Mapped[str] = mapped_column(String(40), unique=True)
    exchange: Mapped[str | None] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(String(20), default="explorer")  # explorer | developer | producer | royalty
    primary_metals: Mapped[str] = mapped_column(String(80), default="")  # csv of metal codes
    jurisdiction: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    sponsored: Mapped[bool] = mapped_column(default=False)  # must be disclosed on the card
    held_by_owner: Mapped[bool] = mapped_column(default=False)  # must be disclosed on the card
    notes: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str | None] = mapped_column(String(10))          # SEC identifier, for automatic filing retrieval
    stage_auto: Mapped[bool] = mapped_column(default=False)      # stage was inferred from facts, not set by a person
    last_filing_seen: Mapped[date | None] = mapped_column(Date)
    projects: Mapped[list["Project"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(200))
    primary_metal: Mapped[str | None] = mapped_column(String(10))
    jurisdiction: Mapped[str | None] = mapped_column(String(120))
    company: Mapped[Company] = relationship(back_populates="projects")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(30))  # technical_report | financials | mdna | presentation | press_release | other
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64))
    effective_date: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    pages_json: Mapped[str] = mapped_column(Text, default="[]")  # page texts, so quotes can always be re-checked


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(60), index=True)
    metal: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str | None] = mapped_column(String(30))
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(20))      # canonical unit after normalisation
    currency: Mapped[str | None] = mapped_column(String(3))
    as_of: Mapped[date | None] = mapped_column(Date)
    doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    page: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str | None] = mapped_column(Text)
    as_written: Mapped[str | None] = mapped_column(String(80))  # e.g. "2.5 Moz"
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(12), default="unverified", index=True)  # unverified | verified | rejected | override
    auto_check: Mapped[str | None] = mapped_column(String(40))  # quote_found | quote_not_found | number_not_in_quote
    note: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    verified_by: Mapped[str | None] = mapped_column(String(10))   # human | auto
    checks: Mapped[str] = mapped_column(Text, default="[]")       # JSON list of {name, passed, detail}
    audited: Mapped[str | None] = mapped_column(String(10))       # pending | pass | fail (spot-checks of auto-verified facts)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PriceDeck(Base):
    __tablename__ = "price_decks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))  # spot | conservative | custom ...
    as_of: Mapped[date] = mapped_column(Date)
    prices_json: Mapped[str] = mapped_column(Text)  # {"au": 4600, "cu": 5.1, ...} $/oz or $/lb
    note: Mapped[str | None] = mapped_column(Text)


class Benchmark(Base):
    """Peer / precedent-transaction benchmarks. Explorers can only be valued against these, so
    until rows exist the explorer valuation is reported as 'not rated'."""
    __tablename__ = "benchmarks"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # peer_ev_per_unit | precedent_per_unit
    metal: Mapped[str] = mapped_column(String(10))
    stage: Mapped[str] = mapped_column(String(20))
    p25: Mapped[float] = mapped_column(Float)
    median: Mapped[float] = mapped_column(Float)
    p75: Mapped[float] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer)
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)


class JurisdictionScore(Base):
    """One row per jurisdiction. `score` (0-100, higher is better) is the policy score used for grading."""
    __tablename__ = "jurisdiction_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120), unique=True, default=lambda ctx: _jkey(ctx.get_current_parameters()["name"]))
    score: Mapped[float] = mapped_column(Float)
    iai: Mapped[float | None] = mapped_column(Float)          # overall investment attractiveness (policy + mineral potential)
    ppi_rank: Mapped[int | None] = mapped_column(Integer)
    iai_rank: Mapped[int | None] = mapped_column(Integer)
    rank_of: Mapped[int | None] = mapped_column(Integer)
    low_sample: Mapped[bool] = mapped_column(default=False)    # Fraser marks rows with only 5-9 responses
    edition: Mapped[str | None] = mapped_column(String(10))
    page: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    price: Mapped[float | None] = mapped_column(Float)
    price_date: Mapped[date | None] = mapped_column(Date)
    card_json: Mapped[str] = mapped_column(Text)  # the whole card as published that week
    quality_score: Mapped[float | None] = mapped_column(Float)
    value_score: Mapped[float | None] = mapped_column(Float)
    published: Mapped[bool] = mapped_column(default=False)  # built from verified facts only: the only kind the public site shows
    summary_json: Mapped[str | None] = mapped_column(Text)  # small digest for directory pages, so they never parse whole cards


class Filing(Base):
    """A regulator filing we have seen, so the weekly job is idempotent and every document has a paper trail."""
    __tablename__ = "filings"
    __table_args__ = (UniqueConstraint("company_id", "accession"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    accession: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(20), default="edgar")
    form: Mapped[str] = mapped_column(String(20))
    filed: Mapped[date | None] = mapped_column(Date)
    period: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(12), default="new")   # new | processed | skipped | error
    error: Mapped[str | None] = mapped_column(Text)
    n_docs: Mapped[int] = mapped_column(Integer, default=0)
    n_facts: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")


class MarketPrice(Base):
    __tablename__ = "market_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(40), index=True)
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(60), default="manual")


class FxRate(Base):
    __tablename__ = "fx_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    usd_per_unit: Mapped[float] = mapped_column(Float)
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(60), default="manual")


def make_engine(url: str | None = None):
    url = url or config.DATABASE_URL
    if url.startswith("sqlite"):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine) -> None:
    """create_all never alters an existing table. When a release adds a column, add it to databases made by an older release
    (nullable, booleans default false) so an old file or Replit database keeps working instead of failing on first query."""
    from sqlalchemy import Boolean, inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                if isinstance(col.type, Boolean):
                    ddl += " DEFAULT 0" if engine.dialect.name == "sqlite" else " DEFAULT FALSE"
                conn.execute(text(ddl))


@contextmanager
def session_scope(engine):
    Session_ = sessionmaker(engine, expire_on_commit=False)
    with Session_() as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
