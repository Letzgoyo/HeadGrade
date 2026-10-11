import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def cli(tmp_path, monkeypatch):
    from headgrade import cli as c, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'c.db'}")
    return c


def test_offline_commands_work_together(cli, capsys):
    main = cli.main
    main(["company-add", "--name", "Example Gold", "--ticker", "EGC", "--stage", "producer", "--metals", "au", "--held"])
    main(["company-set-cik", "EGC", "1429937"])
    main(["price-set", "EGC", "5.12", "USD", "--date", "2026-10-09"])
    main(["fx-set", "CAD", "0.7019"])
    main(["deck-set", "--name", "spot", "--as-of", "2026-10-09", "au=4367.35"])
    main(["company-list"])
    main(["queue"])
    main(["audit-sample", "--rate", "0.5"])
    main(["audit-stats"])
    out = capsys.readouterr().out
    assert "EGC" in out and "HELD" in out and "flagged for audit: 0" in out and "'audited': 0" in out
    from headgrade import db, prices
    from sqlalchemy import select
    eng = db.make_engine()
    with db.session_scope(eng) as s:
        assert s.scalar(select(db.Company).where(db.Company.ticker == "EGC")).cik == "0001429937"
        assert prices.latest_price(s, "EGC").price == 5.12 and prices.latest_fx(s)["CAD"] == 0.7019


def test_unknown_company_gives_a_clear_message(cli):
    with pytest.raises(SystemExit) as e:
        cli.main(["company-set-cik", "NOPE", "1"])
    assert "No company with ticker NOPE" in str(e.value)
