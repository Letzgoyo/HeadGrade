import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HEADGRADE_DATA_DIR", ROOT / "data"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'headgrade.db'}")
MODEL = os.getenv("HEADGRADE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("HEADGRADE_EFFORT", "medium")
