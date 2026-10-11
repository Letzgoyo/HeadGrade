import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MINECARDS_DATA_DIR", ROOT / "data"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'minecards.db'}")
MODEL = os.getenv("MINECARDS_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("MINECARDS_EFFORT", "medium")
