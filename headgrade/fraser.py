"""Parse the Fraser Institute Annual Survey of Mining Companies tables from the survey PDF's text.

Table 1 = Investment Attractiveness Index (policy + mineral potential), Table 2 = Policy Perception Index (PPI),
Table 3 = Best Practices Mineral Potential Index. We grade jurisdiction RISK on the policy score (PPI): mineral
potential is judged separately at the asset level. Rows marked * had only 5-9 responses on a question."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import JurisdictionScore
from .jurisdictions import jkey

REGIONS = ["Latin America and the Caribbean Basin", "Latin America", "United States", "Canada", "Australia", "Oceania", "Africa",
           "Argentina", "Asia", "Europe"]
_NUM = r"(?:\d+(?:\.\d+)?|\*\*)"
_RANK = r"(?:\d+/\d+|\*\*)"
ROW = re.compile(rf"^(?P<name>.*?)\s*(?P<scores>{_NUM}(?:\s+{_NUM}){{4}})\s+(?P<ranks>{_RANK}(?:\s+{_RANK}){{4}})\s*$")
TABLE_LINE = re.compile(r"^Table (\d)(?::.*| continued)\s*$", re.M)
SKIP = ("Score Rank", "fraserinstitute.org", "Fraser Institute Annual Survey", "* Between", "** Not Available", "Table ")


@dataclass
class Row:
    name: str
    low_sample: bool
    score: float | None
    rank: int | None
    of: int | None
    page: int


@dataclass
class Parsed:
    tables: dict[int, list[Row]] = field(default_factory=dict)


def _clean_name(raw: str) -> tuple[str, bool]:
    s = re.sub(r"\s+", " ", raw).strip()
    for r in sorted(REGIONS, key=len, reverse=True):
        if s.startswith(r + " "):
            s = s[len(r) + 1:]
            break
    low = "*" in s
    return s.replace("*", "").strip(), low


def parse_page(text: str, page_no: int) -> tuple[int | None, list[Row]]:
    m = TABLE_LINE.search(text)
    table = int(m.group(1)) if m else None
    rows, pending = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or any(line.startswith(p) or p in line and p != "Table " for p in SKIP[:5]) or re.match(r"^\d{4} \d{4}", line):
            continue
        if line.startswith("Table "):
            continue
        d = ROW.match(line)
        if d:
            name, low = _clean_name(" ".join(pending + [d.group("name")]))
            first = d.group("scores").split()[0]
            rank = d.group("ranks").split()[0]
            rk, of = (int(rank.split("/")[0]), int(rank.split("/")[1])) if "/" in rank else (None, None)
            rows.append(Row(name, low, None if first == "**" else float(first), rk, of, page_no))
            pending = []
        else:
            pending.append(line)
    return table, rows


def parse_pages(pages: list[str], first_page_no: int = 1) -> Parsed:
    out = Parsed()
    for i, text in enumerate(pages):
        table, rows = parse_page(text, first_page_no + i)
        if table and rows:
            out.tables.setdefault(table, []).extend(rows)
    return out


def parse_pdf(path: str, first: int = 15, last: int = 28) -> Parsed:
    from pypdf import PdfReader
    r = PdfReader(path)
    pages = [r.pages[i].extract_text() or "" for i in range(first - 1, min(last, len(r.pages)))]
    return parse_pages(pages, first)


def load(session: Session, parsed: Parsed, edition: str = "2025") -> dict:
    ppi = {jkey(r.name): r for r in parsed.tables.get(2, [])}
    iai = {jkey(r.name): r for r in parsed.tables.get(1, [])}
    n_new = n_upd = 0
    src = f"Fraser Institute Annual Survey of Mining Companies, {edition}: Policy Perception Index (Table 2) and Investment Attractiveness Index (Table 1)"
    for k, r in ppi.items():
        if r.score is None:
            continue
        row = session.scalar(select(JurisdictionScore).where(JurisdictionScore.key == k))
        vals = dict(name=r.name, key=k, score=r.score, ppi_rank=r.rank, rank_of=r.of, low_sample=r.low_sample,
                    iai=iai[k].score if k in iai else None, iai_rank=iai[k].rank if k in iai else None,
                    edition=edition, source=src, page=r.page)
        if row:
            for a, b in vals.items():
                setattr(row, a, b)
            n_upd += 1
        else:
            session.add(JurisdictionScore(**vals))
            n_new += 1
    session.flush()
    return {"added": n_new, "updated": n_upd, "ppi_rows": len(ppi), "iai_rows": len(iai)}
