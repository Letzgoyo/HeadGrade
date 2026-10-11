"""Build the company list automatically from public sources instead of typing companies in one by one.
SEC: every ticker is listed in company_tickers.json; each company's submissions file carries its SIC industry code.
We keep mining codes (1000 metal mining, 1040 gold and silver ores, 1090 miscellaneous metal ores). Resumable: results are cached."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import Company
from .edgar import Edgar

MINING_SIC = ("1000", "1040", "1090")


def _cache_path():
    return config.DATA_DIR / "sec_sic_cache.json"


def load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path().write_text(json.dumps(cache))


def scan_sec(edgar: Edgar, session: Session, sic_codes=MINING_SIC, limit: int | None = None, cache: dict | None = None) -> dict:
    """Look up the SIC code of up to `limit` not-yet-scanned companies; add the mining ones. Returns counts."""
    cache = load_cache() if cache is None else cache
    seen_ciks: set[str] = set()
    scanned = added = matched = 0
    for ticker, (cik, title) in edgar.tickers().items():
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)
        info = cache.get(cik)
        if info is None:
            if limit is not None and scanned >= limit:
                continue
            sub = edgar.submissions(cik)
            info = {"sic": str(sub.get("sic") or ""), "exchange": (sub.get("exchanges") or [None])[0], "name": sub.get("name") or title}
            cache[cik] = info
            scanned += 1
        if info["sic"] in sic_codes:
            matched += 1
            if not session.scalar(select(Company).where(Company.cik == cik)) and not session.scalar(select(Company).where(Company.ticker == ticker)):
                session.add(Company(name=info["name"], ticker=ticker, cik=cik, exchange=info["exchange"], stage="explorer",
                                    stage_auto=True, currency="USD", primary_metals=""))
                added += 1
    save_cache(cache)
    return {"scanned_now": scanned, "mining_matched": matched, "added": added, "cached_total": len(cache)}
