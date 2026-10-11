import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from headgrade import db, fraser, grading
from headgrade.facts import F, FactBook
from headgrade.jurisdictions import jkey, resolve_key
from headgrade.valuation import Proj

# Excerpt of the real text layout of Table 2 (Policy Perception Index) from the survey PDF, including its quirks:
# region names glued to the first row, names split across lines, '*' low-sample marks, a footnote glitch (45.881).
TABLE2 = """ Fraser Institute Annual Survey of Mining Companies, 2025 17
fraserinstitute.org
Table 2: Policy Perception Index
Score Rank
2025 2024 2023 2022 2021 2025 2024 2023 2022 2021
 Canada Alberta 96.71 87.80 87.67 92.64 88.77 3/68 9/82 10/86 6/62 10/84
Newfoundland & 
Labrador
93.01 91.84 87.08 94.85 83.00 7/68 6/82 11/86 5/62 18/84
Nunavut 64.96 44.65 39.09 26.47 70.46 43/68 59/82 65/86 55/62 35/84
United States Nevada 100.00 96.14 91.77 100.00 91.77 1/68 4/82 5/86 1/62 6/84
Oceania Indonesia* 62.92 59.75 32.16 ** 44.60 46/68 41/82 72/86 ** 72/84
Papua New 
Guinea*
51.73 46.10 30.38 20.44 45.09 52/68 57/82 74/86 57/62 71/84
Philippines* 39.62 74.91 23.47 ** 27.17 60/68 25/82 79/86 ** 83/84
Africa Ghana* 53.65 54.95 45.881 49.43 64.59 50/68 46/82 58/86 37/62 47/84
Mali 7.36 14.94 33.34 38.55 49.30 67/68 74/82 71/86 48/62 66/84
Namibia 68.94 78.96 68.00 69.35 75.24 41/68 22/82 33/86 26/62 29/84
Dem. Rep. of  
Congo (DRC)
34.02 12.97 24.93 21.30 29.18 63/68 77/82 77/86 56/62 78/84
Latin America 
and the 
Caribbean 
Basin
Bolivia* 34.52 0.00 25.08 41.17 32.31 62/68 82/82 76/86 43/62 77/84
Angola* 37.36 ** 56.36 17.85 ** 61/68 ** 47/86 58/62 **
"""
TABLE1 = """Table 1: Investment Attractiveness Index
Score Rank
2025 2024 2023 2022 2021 2025 2024 2023 2022 2021
Nevada 90.87 88.69 87.93 92.17 87.64 1/68 2/82 2/86 1/62 3/84
Mali 46.58 39.13 38.04 57.42 33.05 64/68 72/82 70/86 44/62 81/84
Namibia 56.21 66.88 56.43 59.88 52.59 51/68 30/82 42/86 38/62 59/84
"""
PROSE = "The Investment Attractiveness Index (IAI) (table 1 and figure 3) is a composite index that combines both"


def parsed():
    return fraser.parse_pages([PROSE, TABLE1, TABLE2], first_page_no=15)


def test_parser_reads_names_scores_ranks_and_tables():
    p = parsed()
    assert set(p.tables) == {1, 2}                       # prose mentioning "table 1" is not mistaken for a table
    ppi = {r.name: r for r in p.tables[2]}
    assert len(ppi) == 13
    assert ppi["Newfoundland & Labrador"].score == 93.01 and ppi["Newfoundland & Labrador"].rank == 7 and ppi["Newfoundland & Labrador"].of == 68
    assert ppi["Papua New Guinea"].low_sample and ppi["Papua New Guinea"].score == 51.73
    assert ppi["Dem. Rep. of Congo (DRC)"].score == 34.02
    assert ppi["Bolivia"].score == 34.52 and ppi["Bolivia"].low_sample           # region glued across four lines is stripped
    assert ppi["Alberta"].score == 96.71 and "Canada" not in "".join(ppi)       # region prefix removed
    assert ppi["Ghana"].score == 53.65                                          # footnote glitch in a later year column is harmless
    assert ppi["Angola"].score == 37.36 and ppi["Mali"].score == 7.36 and not ppi["Mali"].low_sample
    assert ppi["Mali"].page == 17


def test_load_into_database_is_idempotent_and_keeps_both_indices(tmp_path):
    engine = db.make_engine("sqlite:///:memory:")
    db.init_db(engine)
    with db.session_scope(engine) as s:
        r1 = fraser.load(s, parsed())
        r2 = fraser.load(s, parsed())
        assert r1["added"] == 13 and r2["added"] == 0 and r2["updated"] == 13
        from sqlalchemy import select
        mali = s.scalar(select(db.JurisdictionScore).where(db.JurisdictionScore.key == "mali"))
        assert (mali.score, mali.iai, mali.ppi_rank, mali.rank_of, mali.edition) == (7.36, 46.58, 67, 68, "2025")
        assert "Fraser Institute" in mali.source


def test_aliases_match_filing_wording_to_fraser_names():
    assert resolve_key("Democratic Republic of the Congo") == resolve_key("Dem. Rep. of Congo (DRC)")
    assert resolve_key("DRC") == jkey("Dem. Rep. of Congo (DRC)")
    assert resolve_key("Côte d'Ivoire") == jkey("Ivory Coast")
    assert resolve_key("Guinea") == jkey("Guinea (Conakry)") and resolve_key("Ireland") == jkey("Ireland, Republic of")
    assert resolve_key("Newfoundland and Labrador") == jkey("Newfoundland & Labrador")
    assert resolve_key("  MALI ") == "mali" and resolve_key(None) == ""


def table():
    return {jkey(r.name): r.score for r in parsed().tables[2]}


def world(weights=True):
    facts = [F("jurisdiction", value_text="Mali", project_id=1, status="verified"),
             F("jurisdiction", value_text="Namibia", project_id=2, status="verified")]
    if weights:
        facts += [F("project_weight_pct", 43.0, project_id=1, status="verified"), F("project_weight_pct", 57.0, project_id=2, status="verified")]
    return FactBook(facts), [Proj(1, "Fekola", "au"), Proj(2, "Otjikoto", "au")]


def test_jurisdiction_score_is_weighted_by_production_share():
    fb, projects = world(True)
    notes = []
    s = grading._jurisdiction(fb, projects, table(), notes)
    assert s == pytest.approx((7.36 * 43 + 68.94 * 57) / 100)
    assert any("weighted by each project's share" in n for n in notes)
    fb2, projects2 = world(False)
    notes2 = []
    s2 = grading._jurisdiction(fb2, projects2, table(), notes2)
    assert s2 == pytest.approx((7.36 + 68.94) / 2) and any("weighted equally" in n for n in notes2)


def test_heavy_exposure_to_a_weak_jurisdiction_is_a_red_flag():
    fb, projects = world(True)
    flags = grading._red_flags(fb, "producer", {"metrics": {}, "tables": {}}, date(2026, 10, 11), projects, table())
    assert any("43% of production/NAV is in Mali" in f["text"] and f["severity"] == "high" for f in flags)
    fb2, projects2 = world(False)          # no weights stated: no exposure claim is made
    assert not any("Mali" in f["text"] for f in grading._red_flags(fb2, "producer", {"metrics": {}, "tables": {}}, date(2026, 10, 11), projects2, table()))
