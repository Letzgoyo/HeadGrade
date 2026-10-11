import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from headgrade import db, extract, ingest, units
from headgrade.extract import ExtractedFact, ExtractionResult, normalise_text, numbers_in, select_pages, verify_fact

PAGES = [
    "EXAMPLE GOLD CORP. Technical Report\nSummary of the Alpha Project",
    "Table 14-1 Mineral Resource Estimate\nIndicated 45.2 Mt 1.12 g/t Au 1.63 Moz\nInferred 30.1 Mt 0.95 g/t Au 0.92 Moz\n",
    "The after-tax NPV (5%) is US$1,800 million and the after-tax IRR is 24.1%. Initial capital cost is US$900 million.\n"
    "The study assumes a gold price of US$2,000/oz. Mine life is 12.5 years.",
    "Item 22 permitting: the Project is in process of obtaining its environmental approvals.",
]


def ef(**kw):
    base = dict(key="npv_after_tax", project="Alpha", value=1800.0, unit="US$ millions", page=3,
                quote="after-tax NPV (5%) is US$1,800 million")
    base.update(kw)
    return ExtractedFact(**base)


def test_text_normalisation_and_number_extraction():
    assert normalise_text("After‑tax  NPV is “US$1,800”") == "after‑tax npv is \"us$1,800\""
    assert numbers_in("NPV (5%) is US$1,800 million, IRR 24.1%") == [5.0, 1800.0, 24.1]


def test_verification_catches_invented_quotes_and_wrong_numbers():
    assert verify_fact(ef(), PAGES) == ("quote_found", 3)
    assert verify_fact(ef(page=2), PAGES) == ("quote_found", 3)                    # off by one page is tolerated and corrected
    assert verify_fact(ef(quote="the after-tax NPV is US$2,500 million"), PAGES)[0] == "quote_not_found"   # invented
    assert verify_fact(ef(value=1900.0), PAGES)[0] == "number_not_in_quote"        # real quote, wrong number
    assert verify_fact(ef(quote="x"), PAGES)[0] == "quote_not_found"
    txt = ExtractedFact(key="permit_stage", project="Alpha", text="in_process", page=4,
                        quote="the Project is in process of obtaining its environmental approvals")
    assert verify_fact(txt, PAGES)[0] == "quote_found"                              # text facts skip the number test


def test_page_selection_keeps_summary_and_dense_pages():
    pages = [f"filler page {i}" for i in range(1, 200)]
    pages[119] = "Mineral resource estimate measured indicated inferred proven probable 1,234 5,678"
    chosen = select_pages(pages, "technical_report", max_pages=10)
    assert len(chosen) <= 13 and {1, 2, 3} <= set(chosen) and 120 in chosen
    assert select_pages(PAGES, "technical_report") == [1, 2, 3, 4]


class FakeClient:
    def __init__(self, facts):
        self.calls = []
        self.messages = NS(parse=self._parse)
        self._facts = facts

    def _parse(self, **kw):
        self.calls.append(kw)
        return NS(parsed_output=ExtractionResult(facts=self._facts), stop_reason="end_turn")


@pytest.fixture
def session():
    engine = db.make_engine("sqlite:///:memory:")
    db.init_db(engine)
    with db.session_scope(engine) as s:
        c = db.Company(name="Example Gold Corp", ticker="EGC.TO", stage="developer", currency="USD")
        s.add(c)
        s.flush()
        yield s, c


def test_ingest_saves_verified_facts_normalises_units_and_rejects_fabrications(session):
    s, c = session
    facts = [
        ef(),                                                                           # good: US$ millions -> USD
        ef(key="initial_capex", value=900.0, quote="Initial capital cost is US$900 million"),
        ef(key="study_price", metal="au", value=2000.0, unit="US$/oz", quote="gold price of US$2,000/oz"),
        ef(key="resource_contained", metal="au", category="indicated", value=1.63, unit="Moz", page=2,
           quote="Indicated 45.2 Mt 1.12 g/t Au 1.63 Moz"),
        ef(key="mine_life_years", value=12.5, unit="years", quote="Mine life is 12.5 years"),
        ef(key="permit_stage", value=None, text="in process", unit=None, quote="the Project is in process of obtaining its environmental approvals", page=4),
        ef(key="irr_after_tax", value=31.0, unit="%", quote="after-tax IRR is 24.1%"),                 # number not in quote
        ef(key="npv_discount_rate", value=8.0, unit="%", quote="discounted at eight percent per year"),   # fabricated
        ef(),                                                                           # exact duplicate
    ]
    client = FakeClient(facts)
    summary = ingest.ingest_pages(s, c, PAGES, "technical_report", "Alpha PFS", client, date(2026, 3, 1))
    assert (summary.extracted, summary.saved, summary.auto_rejected, summary.skipped_duplicates) == (9, 8, 1, 1)
    from sqlalchemy import select
    rows = {(r.key, r.metal, r.category): r for r in s.scalars(select(db.Fact))}
    assert rows[("npv_after_tax", None, None)].value_num == 1.8e9 and rows[("npv_after_tax", None, None)].currency == "USD"
    assert rows[("initial_capex", None, None)].value_num == 900e6
    assert rows[("study_price", "au", None)].value_num == 2000.0 and rows[("study_price", "au", None)].unit == "oz"
    assert rows[("resource_contained", "au", "indicated")].value_num == pytest.approx(1.63e6)
    assert rows[("resource_contained", "au", "indicated")].unit == "oz"
    assert rows[("permit_stage", None, None)].value_text == "in_process"
    assert rows[("irr_after_tax", None, None)].auto_check == "number_not_in_quote" and rows[("irr_after_tax", None, None)].confidence <= 0.3
    assert rows[("npv_discount_rate", None, None)].status == "rejected"
    assert all(r.status in ("unverified", "rejected") for r in rows.values())     # nothing is verified without a human
    assert s.scalar(select(db.Project).where(db.Project.name == "Alpha")) is not None      # project created from extraction
    # the model was shown only allowed keys and told the pages are data
    assert "treat as data" in client.calls[0]["messages"][0]["content"] and "npv_after_tax" in client.calls[0]["messages"][0]["content"]
    assert client.calls[0]["model"] and client.calls[0]["output_format"] is ExtractionResult


def test_ingest_is_idempotent_for_the_same_document(session):
    s, c = session
    client = FakeClient([ef()])
    a = ingest.ingest_pages(s, c, PAGES, "technical_report", "Alpha PFS", client)
    b = ingest.ingest_pages(s, c, PAGES, "technical_report", "Alpha PFS", client)
    assert a.saved == 1 and b.saved == 0 and len(client.calls) == 1


def test_study_stage_and_unit_mismatch_are_flagged(session):
    s, c = session
    client = FakeClient([
        ef(key="study_stage", value=None, text="Pre-Feasibility Study", unit=None, quote="Summary of the Alpha Project", page=1),
        ef(key="resource_contained", metal="au", category="indicated", value=45.2, unit="Mt", page=2,
           quote="Indicated 45.2 Mt 1.12 g/t Au 1.63 Moz"),
    ])
    ingest.ingest_pages(s, c, PAGES, "technical_report", "Alpha PFS", client)
    from sqlalchemy import select
    rows = {r.key: r for r in s.scalars(select(db.Fact))}
    assert rows["study_stage"].value_text == "PFS"
    assert "not oz" in rows["resource_contained"].note and rows["resource_contained"].confidence <= 0.5


def test_real_pdf_roundtrip(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    path = tmp_path / "report.pdf"
    with PdfPages(path) as pdf:
        for text in PAGES[:3]:
            fig = plt.figure(figsize=(8.5, 11))
            fig.text(0.05, 0.9, text.replace("\n", " ").replace("$", r"\$"), fontsize=9)
            pdf.savefig(fig)
            plt.close(fig)
    pages = extract.load_pdf_pages(str(path))
    assert len(pages) == 3
    assert "Mineral Resource Estimate" in pages[1].replace("\n", " ")


def test_as_written_keeps_readable_numbers(session):
    s, c = session
    client = FakeClient([ef(key="shares_basic", value=1332417631.0, unit=None, quote="x" * 3 + " 1,332,417,631 shares", page=1),
                         ef(key="revenue_ttm", value=3061238.0, unit="US$ thousands", quote="Gold revenue 3,061,238", page=1)])
    ingest.ingest_pages(s, c, ["x" * 3 + " 1,332,417,631 shares and Gold revenue 3,061,238"], "financials", "t", client)
    from sqlalchemy import select
    written = {r.key: r.as_written for r in s.scalars(select(db.Fact))}
    assert written["shares_basic"] == "1,332,417,631" and written["revenue_ttm"] == "3,061,238 US$ thousands"
