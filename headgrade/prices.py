"""Share prices, FX and metal decks entered by hand or fetched, with provenance. For a paid public product,
replace the sources with a LICENSED feed (see README); the rest of the system only reads these tables."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import FxRate, MarketPrice


def set_price(session: Session, ticker: str, price: float, currency: str, as_of: date | None = None, source: str = "manual") -> None:
    session.add(MarketPrice(ticker=ticker.upper(), price=price, currency=currency.upper(), as_of=as_of or date.today(), source=source))


def latest_price(session: Session, ticker: str) -> MarketPrice | None:
    return session.scalars(select(MarketPrice).where(MarketPrice.ticker == ticker.upper())
                           .order_by(MarketPrice.as_of.desc(), MarketPrice.id.desc())).first()


def set_fx(session: Session, currency: str, usd_per_unit: float, as_of: date | None = None, source: str = "manual") -> None:
    session.add(FxRate(currency=currency.upper(), usd_per_unit=usd_per_unit, as_of=as_of or date.today(), source=source))


def latest_fx(session: Session) -> dict[str, float]:
    out = {"USD": 1.0}
    for r in session.scalars(select(FxRate).order_by(FxRate.as_of, FxRate.id)):
        out[r.currency] = r.usd_per_unit
    return out


BOC_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json?recent=1"


def parse_boc(text: str) -> tuple[date, float]:
    """Bank of Canada Valet response -> (date, USD per 1 CAD). The series is CAD per 1 USD, so we invert it.
    NOTE: written from the documented shape, not yet confirmed against a live response."""
    try:
        obs = json.loads(text)["observations"][-1]
        key = next(k for k in obs if k != "d")
        cad_per_usd = float(obs[key]["v"])
        return date.fromisoformat(obs["d"]), 1.0 / cad_per_usd
    except (KeyError, IndexError, StopIteration, ValueError, TypeError) as exc:
        raise ValueError(f"unexpected Bank of Canada response: {exc}") from exc


def fetch_boc(getter) -> tuple[date, float]:
    raw = getter(BOC_URL)
    return parse_boc(raw.decode() if isinstance(raw, bytes) else raw)
