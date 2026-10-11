"""Turn 'as written' values ('2.5', 'Moz') into canonical numbers (2,500,000 oz)."""
from __future__ import annotations

import re

_COMPOUND = {
    "moz": (1e6, "oz"), "koz": (1e3, "oz"), "oz": (1, "oz"), "ozs": (1, "oz"), "ounces": (1, "oz"), "ounce": (1, "oz"),
    "mlb": (1e6, "lb"), "mlbs": (1e6, "lb"), "klb": (1e3, "lb"), "blb": (1e9, "lb"), "lb": (1, "lb"), "lbs": (1, "lb"),
    "pounds": (1, "lb"), "mt": (1e6, "t"), "kt": (1e3, "t"), "t": (1, "t"), "tonnes": (1, "t"), "tonne": (1, "t"),
    "g/t": (1, "g/t"), "gpt": (1, "g/t"), "g/tonne": (1, "g/t"), "ppm": (1, "g/t"), "%": (1, "%"),
}
_MULT = [(r"\b(billion|billions|bn|b)\b", 1e9), (r"\b(million|millions|mm|mn|m)\b", 1e6), (r"\b(thousand|thousands|k)\b", 1e3)]
_CCY = [(r"\bus\$|\busd\b|us dollars?", "USD"), (r"\bc\$|\bcad\b|canadian", "CAD"), (r"\ba\$|\baud\b|australian", "AUD")]


def parse_unit(unit: str | None) -> tuple[float, str | None, str | None]:
    """Returns (multiplier, base_unit, currency)."""
    s = (unit or "").lower().strip()
    ccy = next((c for pat, c in _CCY if re.search(pat, s)), None)
    mult, base = 1.0, None
    for token in re.split(r"[\s,]+", s):
        if token in _COMPOUND:
            m, b = _COMPOUND[token]
            mult *= m
            base = base or b
    if base is None or mult == 1.0:  # words like 'million' alongside a plain unit
        for pat, m in _MULT:
            if re.search(pat, s) and not any(t in ("moz", "mlb", "mlbs", "mt", "blb") for t in re.split(r"[\s,]+", s)):
                mult *= m
                break
    return mult, base, ccy


def normalize(value: float, unit: str | None) -> tuple[float, str | None, str | None]:
    """(canonical value, canonical base unit, currency)."""
    mult, base, ccy = parse_unit(unit)
    return value * mult, base, ccy
