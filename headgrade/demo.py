"""Demo mode: a throwaway database of FICTIONAL companies, so the whole site can be tried with no API key, no network and no cost."""
from __future__ import annotations

import sys
from pathlib import Path

from . import config, db

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def demo_engine():
    out = config.DATA_DIR / "demo_site"
    path = out / "demo.db"
    if not path.exists():
        sys.path.insert(0, str(EXAMPLES))
        old = config.DATA_DIR
        try:
            import seed_site_demo
            seed_site_demo.main(out)
        finally:
            config.DATA_DIR = old      # the demo builder repoints this; put it back
    return db.make_engine(f"sqlite:///{path}")
