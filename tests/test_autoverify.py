import json
import random
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headgrade import autoverify as av, db, review
from headgrade.extract import ExtractedFact as EF


@pytest.fixture
def world():
    engine = db.make_engine("sqlite:///:memory:")
    db.init_db(engine)
    with db.session_scope(engine) as s:
        co = db.Company(name="Example Corp", ticker="EXC", stage="producer", currency="USD")
        s.add(co)
        s.flush()
        p = db.Project(company_id=co.id, name="Alpha Project")
        d1 = db.Document(company_id=co.id, kind="financials", title="Q2", effective_date=date(2026, 6, 30), pages_json="[]")
        d2 = db.Document(company_id=co.id, kind="mdna", title="MD&A", effective_date=date(2026, 6, 30), pages_json="[]")
        s.add_all([p, d1, d2])
        s.flush()
        s.refresh(co)
        yield s, co, p, d1, d2


def fact(s, co, doc, key, value=None, text=None, project=None, metal=None, category=None, as_of=date(2026, 6, 30),
         auto_check="quote_found", status="unverified", **kw):
    f = db.Fact(company_id=co.id, project_id=project.id if project else None, doc_id=doc.id, key=key, metal=metal, category=category,
                value_num=value, value_text=text, as_of=as_of, auto_check=auto_check, status=status, page=1, quote="q", confidence=0.8, **kw)
    s.add(f)
    s.flush()
    return f


def checks(f):
    return {c["name"]: c for c in json.loads(f.checks)}


def test_second_reading_agreement_auto_verifies_and_records_why(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "cash", 287e6, currency="USD")
    st = av.apply(s, co, d1.id, [EF(key="cash", value=287.0, unit="US$ millions", page=1, quote="x")])
    assert (f.status, f.verified_by, st.auto_verified) == ("verified", "auto", 1)
    c = checks(f)
    assert c["second_reading"]["passed"] and c["quote_on_page"]["passed"]


def test_units_in_the_second_reading_are_normalised_before_comparing(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "revenue_ttm", 3_061_238_000.0, currency="USD")
    av.apply(s, co, d1.id, [EF(key="revenue_ttm", value=3.06, unit="US$ billion", page=1, quote="x")])   # 3.06B vs 3.061238B: within 0.5%
    assert f.status == "verified"


def test_second_reading_conflict_goes_to_a_person_with_the_reason(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "cash", 287e6, currency="USD")
    st = av.apply(s, co, d1.id, [EF(key="cash", value=387.0, unit="US$ millions", page=1, quote="x")])
    assert f.status == "unverified" and st.needs_review == 1
    assert "NEEDS REVIEW" in f.note and not checks(f)["second_reading"]["passed"]
    assert "387.000M" in f.note and "287.000M" in f.note       # the reviewer sees BOTH values, not just "different"
    assert review.priority(f) == 0                        # shown first in the review queue


def test_nothing_confirms_it_means_unconfirmed_not_verified(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "cash", 287e6, currency="USD")
    st = av.apply(s, co, d1.id, [])                        # second reading found nothing for this key
    assert f.status == "unverified" and st.unconfirmed == 1 and not f.note
    st2 = av.apply(s, co, d1.id, None)                     # no second reading at all
    assert f.status == "unverified" and st2.unconfirmed == 1


def test_cross_document_agreement_confirms_without_a_second_reading(world):
    s, co, p, d1, d2 = world
    other = fact(s, co, d2, "shares_basic", 1_332_417_631.0, as_of=date(2026, 6, 28))
    f = fact(s, co, d1, "shares_basic", 1_332_417_631.0, as_of=date(2026, 6, 30))
    av.apply(s, co, d1.id, None)
    assert f.status == "verified" and checks(f)["cross_document"]["passed"]


def test_cross_document_conflict_on_the_same_date_needs_review(world):
    s, co, p, d1, d2 = world
    fact(s, co, d2, "cash", 380e6, currency="USD", as_of=date(2026, 6, 30))
    f = fact(s, co, d1, "cash", 287e6, currency="USD", as_of=date(2026, 6, 30))
    av.apply(s, co, d1.id, [EF(key="cash", value=287.0, unit="US$ millions", page=1, quote="x")])   # second reading agrees, but another document disagrees
    assert f.status == "unverified" and not checks(f)["cross_document"]["passed"]
    assert "different figure" in f.note


def test_figures_for_different_dates_are_not_a_conflict(world):
    s, co, p, d1, d2 = world
    fact(s, co, d2, "cash", 380e6, currency="USD", as_of=date(2025, 12, 31))     # year-end balance, not June
    f = fact(s, co, d1, "cash", 287e6, currency="USD", as_of=date(2026, 6, 30))
    av.apply(s, co, d1.id, [EF(key="cash", value=287.0, unit="US$ millions", page=1, quote="x")])
    assert f.status == "verified"


def test_implausible_values_are_blocked_even_when_a_second_reading_agrees(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "aisc_per_unit", 25000.0, metal="au")
    av.apply(s, co, d1.id, [EF(key="aisc_per_unit", metal="au", value=25000.0, unit="US$/oz", page=1, quote="x")])
    assert f.status == "unverified" and not checks(f)["plausible_range"]["passed"] and review.priority(f) == 1
    assert av.plausible("shares_basic", None, 1.3e9)[0] is True and av.plausible("cash", None, None)[0] is None
    assert av.plausible("study_price", "ag", 28.0)[0] is True and av.plausible("study_price", "ag", 2800.0)[0] is False


def test_a_quote_whose_number_does_not_match_is_never_auto_verified(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "cash", 287e6, currency="USD", auto_check="number_not_in_quote")
    av.apply(s, co, d1.id, [EF(key="cash", value=287.0, unit="US$ millions", page=1, quote="x")])
    assert f.status == "unverified" and not checks(f)["quote_on_page"]["passed"]


def test_resource_arithmetic_must_add_up(world):
    s, co, p, d1, d2 = world
    rows = [fact(s, co, d1, "resource_contained", v, project=p, metal="au", category=c)
            for c, v in (("measured", 400e3), ("indicated", 800e3), ("measured_indicated", 1_900e3))]    # 400+800 != 1900
    sec = [EF(key="resource_contained", project="Alpha Project", metal="au", category=c, value=v / 1e3, unit="koz", page=1, quote="x")
           for c, v in (("measured", 400e3), ("indicated", 800e3), ("measured_indicated", 1_900e3))]
    av.apply(s, co, d1.id, sec)
    assert all(r.status == "unverified" and "does not equal" in r.note for r in rows)
    # when it adds up, all three auto-verify
    rows[2].value_num = 1_200e3
    rows[2].status, rows[2].note, rows[2].checks = "unverified", None, "[]"
    for r in rows:
        r.status, r.note = "unverified", None
    sec[2].value = 1200.0
    av.apply(s, co, d1.id, sec)
    assert all(r.status == "verified" for r in rows)


def test_irr_must_be_consistent_with_npv_and_discount_rate(world):
    s, co, p, d1, d2 = world
    npv = fact(s, co, d1, "npv_after_tax", 1.8e9, project=p, currency="USD")
    rate = fact(s, co, d1, "npv_discount_rate", 5.0, project=p)
    irr = fact(s, co, d1, "irr_after_tax", 2.0, project=p)                 # positive NPV but IRR below the discount rate
    sec = [EF(key="npv_after_tax", project="Alpha Project", value=1800, unit="US$ millions", page=1, quote="x"),
           EF(key="npv_discount_rate", project="Alpha Project", value=5, unit="%", page=1, quote="x"),
           EF(key="irr_after_tax", project="Alpha Project", value=2, unit="%", page=1, quote="x")]
    av.apply(s, co, d1.id, sec)
    assert all(r.status == "unverified" and "inconsistent" in r.note for r in (npv, rate, irr))


def test_text_facts_and_project_matching(world):
    s, co, p, d1, d2 = world
    f = fact(s, co, d1, "study_stage", text="PFS", project=p)
    av.apply(s, co, d1.id, [EF(key="study_stage", project="Alpha", text="Pre-Feasibility Study", page=1, quote="x")])   # normalised to PFS; 'Alpha' matches 'Alpha Project'
    assert f.status == "verified"
    g = fact(s, co, d1, "permit_stage", text="approved", project=p)
    av.apply(s, co, d1.id, [EF(key="permit_stage", project="Beta Project", text="approved", page=1, quote="x")])        # a different project's fact does not confirm this one
    assert g.status == "unverified"


def test_people_are_never_overridden_and_rejected_facts_are_ignored(world):
    s, co, p, d1, d2 = world
    human = fact(s, co, d1, "cash", 287e6, currency="USD", status="verified", verified_by="human")
    rej = fact(s, co, d1, "debt", 5.0, status="rejected")
    av.apply(s, co, d1.id, [EF(key="cash", value=999.0, unit="US$ millions", page=1, quote="x")])
    assert (human.status, human.verified_by, human.value_num) == ("verified", "human", 287e6) and rej.status == "rejected"


def test_audit_sampling_measures_and_corrects_the_machine(world):
    s, co, p, d1, d2 = world
    fs = [fact(s, co, d1, "shares_basic", 1e9 + i, status="verified", verified_by="auto") for i in range(200)]
    n = review.sample_for_audit(s, rate=0.1, seed=7)
    assert 5 <= n <= 40 and len(review.audit_queue(s)) == n
    pend = review.audit_queue(s)
    review.record_audit(s, pend[0].id, passed=False, note="table misread")
    for f in pend[1:]:
        review.record_audit(s, f.id, passed=True)
    st = review.audit_stats(s)
    assert st["failed"] == 1 and st["passed"] == n - 1 and st["error_rate"] == pytest.approx(1 / n) and st["pending"] == 0
    assert pend[0].status == "unverified" and pend[0].verified_by is None and "AUDIT FAILED" in pend[0].note   # pulled out of published cards
    human = [f for f in fs if f.audited is None][0]
    review.set_status(s, human.id, "verified")
    assert human.verified_by == "human"


def test_closeness_accounts_for_how_precisely_each_number_was_written():
    assert av.close(3.06e9, 3_061_238_000.0)             # "3.06 billion" vs a table value in thousands
    assert av.close(3.1e9, 3_061_238_000.0)              # coarser rounding still agrees
    assert av.close(287e6, 287_000_000.0) and av.close(1_332_417_631.0, 1_332_417_631.0)
    assert not av.close(2356.0, 2365.0)                  # transposed digits: both written to the unit
    assert not av.close(1e9, 1.4e9)                      # "1 billion" does not agree with 1.4 billion
    assert not av.close(287e6, 387e6) and not av.close(None, 5.0)
    assert av.close(0.1 * 3, 0.3)                        # float noise is ignored
