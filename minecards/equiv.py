"""Convert multi-metal contained quantities to one equivalent unit, valued at a deck.
AuEq = sum(qty_i * recovery_i * price_i) / price_au. Approximate: ignores payability and refining terms."""
from __future__ import annotations

from .decks import Deck


def equivalent_units(contained: dict[str, float], deck: Deck, to: str = "au",
                     recoveries: dict[str, float] | None = None) -> tuple[float, list[str]]:
    """contained: metal -> canonical units (oz or lb). Returns (units of `to`, notes)."""
    notes: list[str] = []
    recoveries = recoveries or {}
    total = 0.0
    for metal, qty in contained.items():
        if metal == to:
            rec = recoveries.get(metal, 1.0)
        else:
            rec = recoveries.get(metal)
            if rec is None:
                rec = 1.0
                notes.append(f"no recovery stated for {metal.upper()}: assumed 100% (overstates {to.upper()}-equivalent)")
        total += qty * rec * deck.price(metal)
    return total / deck.price(to), notes
