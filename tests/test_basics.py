from headgrade import units
from headgrade.facts import F, FactBook
from headgrade.registry import REGISTRY, METAL_UNIT


def test_unit_normalisation():
    assert units.normalize(2.5, "Moz") == (2.5e6, "oz", None)
    assert units.normalize(410, "koz") == (410_000, "oz", None)
    assert units.normalize(1.2, "Blb") == (1.2e9, "lb", None)
    assert units.normalize(0.85, "g/t") == (0.85, "g/t", None)
    assert units.normalize(450, "US$ millions") == (450e6, None, "USD")
    assert units.normalize(1.3, "C$ billion") == (1.3e9, None, "CAD")
    assert units.normalize(32, "million ounces") == (32e6, "oz", None)
    assert units.normalize(55, "million") == (55e6, None, None)
    assert units.normalize(18.5, "%") == (18.5, "%", None)


def test_registry_is_consistent():
    for key, spec in REGISTRY.items():
        assert spec.key == key and spec.family and spec.scope in ("company", "project")
        if spec.family == "metal_qty":
            assert spec.needs_metal
    assert set(METAL_UNIT.values()) == {"oz", "lb"}


def test_factbook_prefers_verified_then_newest_and_tracks_inputs():
    from datetime import date
    fb = FactBook([
        F("cash", 5e6, status="unverified", as_of=date(2026, 6, 30)),
        F("cash", 4e6, status="verified", as_of=date(2026, 3, 31), id=7),
        F("cash", 1e6, status="rejected"),
        F("debt", 2e6, status="override"),
    ])
    assert fb.num("cash") == 4e6                       # verified beats a newer unverified fact
    assert fb.num("debt") == 2e6 and fb.num("shares_basic") is None
    c = fb.confidence()
    assert c["inputs_used"] == 2 and c["verified"] == 2 and c["missing"] == 1
    fb2 = FactBook([F("cash", 5e6)], include_unverified=False)
    assert fb2.num("cash") is None                     # verified-only mode ignores unverified facts


def test_init_db_adds_columns_missing_from_an_older_database(tmp_path):
    import sqlite3
    from sqlalchemy import inspect, text
    from headgrade import db
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, company_id INTEGER, taken_at DATETIME, price FLOAT, price_date DATE, card_json TEXT, quality_score FLOAT, value_score FLOAT)")
    con.execute("INSERT INTO snapshots (id, company_id, card_json) VALUES (1, 1, '{}')")
    con.commit()
    con.close()
    eng = db.make_engine(f"sqlite:///{path}")
    db.init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("snapshots")}
    assert {"published", "summary_json"} <= cols
    with eng.connect() as c:
        assert c.execute(text("SELECT published FROM snapshots")).scalar() == 0
    db.init_db(eng)                       # idempotent
