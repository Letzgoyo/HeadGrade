"""Metal price decks. Prices are $/oz (au, ag, pt, pd) or $/lb (cu, ni, u3o8, zn, pb).
There are no built-in 'current' prices on purpose: you set the deck, and the card shows its date."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .registry import METAL_UNIT


@dataclass
class Deck:
    name: str
    prices: dict[str, float]
    as_of: date | None = None

    def price(self, metal: str) -> float:
        if metal not in self.prices:
            raise KeyError(f"deck '{self.name}' has no price for {metal}")
        return self.prices[metal]

    def scaled(self, m: float, name: str | None = None) -> "Deck":
        return Deck(name or f"{self.name} x{m:g}", {k: v * m for k, v in self.prices.items()}, self.as_of)

    def to_json(self) -> str:
        return json.dumps(self.prices)

    @staticmethod
    def from_row(row) -> "Deck":
        return Deck(row.name, json.loads(row.prices_json), row.as_of)

    def describe(self) -> str:
        return ", ".join(f"{m.upper()} ${p:,.2f}/{METAL_UNIT[m]}" for m, p in self.prices.items())
