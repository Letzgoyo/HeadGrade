"""Matching the jurisdiction names that appear in company filings to the names in the Fraser survey."""
from __future__ import annotations

import re
import unicodedata


def jkey(name: str | None) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# alias (as it may appear in a filing) -> the Fraser name. Compared after jkey() on both sides.
_ALIASES = {
    "DRC": "Dem. Rep. of Congo (DRC)", "Democratic Republic of the Congo": "Dem. Rep. of Congo (DRC)",
    "Democratic Republic of Congo": "Dem. Rep. of Congo (DRC)", "Congo (DRC)": "Dem. Rep. of Congo (DRC)",
    "Ivory Coast": "Ivory Coast", "Cote d'Ivoire": "Ivory Coast",
    "Guinea": "Guinea (Conakry)", "Republic of Guinea": "Guinea (Conakry)",
    "Ireland": "Ireland, Republic of", "Republic of Ireland": "Ireland, Republic of",
    "Newfoundland and Labrador": "Newfoundland & Labrador", "Labrador": "Newfoundland & Labrador", "Newfoundland": "Newfoundland & Labrador",
    "NWT": "Northwest Territories", "NSW": "New South Wales", "WA": "Western Australia", "SA": "South Australia",
    "BC": "British Columbia", "PNG": "Papua New Guinea", "Neuquén": "Neuquen",
}
ALIAS_KEYS = {jkey(a): jkey(c) for a, c in _ALIASES.items()}


def resolve_key(name: str | None) -> str:
    k = jkey(name)
    return ALIAS_KEYS.get(k, k)
