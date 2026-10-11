"""FactBook: the engine's view of the facts. Decoupled from the database so valuation and grading
can be tested with plain objects. Preference order: override > verified > unverified; then newest."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

STATUS_RANK = {"override": 3, "verified": 2, "unverified": 1}


@dataclass
class F:
    key: str
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    currency: str | None = None
    metal: str | None = None
    category: str | None = None
    project_id: int | None = None
    as_of: date | None = None
    status: str = "unverified"
    confidence: float = 0.5
    id: int | None = None
    doc_id: int | None = None
    page: int | None = None
    quote: str | None = None
    created_order: int = 0


@dataclass
class Used:
    """One input a calculation relied on (shown on the card, drives the confidence score)."""
    key: str
    label: str
    value: float | str | None
    status: str
    fact_id: int | None = None


class FactBook:
    def __init__(self, facts: list[F], include_unverified: bool = True):
        self.facts = [f for f in facts if f.status != "rejected" and (include_unverified or f.status in ("verified", "override"))]
        self.used: list[Used] = []
        self.missing: list[str] = []

    def _match(self, key, project_id, metal, category):
        out = [f for f in self.facts if f.key == key
               and (project_id is None or f.project_id in (project_id, None))
               and (metal is None or f.metal == metal)
               and (category is None or f.category == category)]
        out.sort(key=lambda f: (STATUS_RANK.get(f.status, 0), f.as_of or date.min, f.created_order), reverse=True)
        return out

    def get(self, key, project_id=None, metal=None, category=None, label=None, record=True) -> F | None:
        m = self._match(key, project_id, metal, category)
        lab = label or key
        if not m:
            if record:
                self.missing.append(lab)
            return None
        f = m[0]
        if record:
            self.used.append(Used(key, lab, f.value_num if f.value_num is not None else f.value_text, f.status, f.id))
        return f

    def num(self, key, default=None, **kw) -> float | None:
        f = self.get(key, **kw)
        return f.value_num if f is not None and f.value_num is not None else default

    def text(self, key, default=None, **kw) -> str | None:
        f = self.get(key, **kw)
        return f.value_text if f is not None and f.value_text is not None else default

    def all(self, key, **kw) -> list[F]:
        return self._match(key, kw.get("project_id"), kw.get("metal"), kw.get("category"))

    def confidence(self) -> dict:
        """Share of the inputs actually used that you have verified (or overridden)."""
        n = len(self.used)
        ok = sum(1 for u in self.used if u.status in ("verified", "override"))
        return {"inputs_used": n, "verified": ok, "unverified": n - ok, "missing": len(set(self.missing)),
                "verified_share": round(ok / n, 2) if n else 0.0}
