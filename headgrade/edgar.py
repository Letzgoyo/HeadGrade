"""SEC EDGAR access (free). The SEC asks every client to send a User-Agent that names you and gives contact details,
and to stay under 10 requests a second. Set SEC_USER_AGENT="Your Name your@email.com" (on Replit: a Secret).

Endpoints used: sec.gov/files/company_tickers.json, data.sec.gov/submissions/CIK##########.json,
sec.gov/Archives/edgar/data/<cik>/<accession-without-dashes>/index.json and the documents inside."""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

DEFAULT_FORMS = ("6-K", "40-F", "20-F", "10-K", "10-Q", "8-K", "6-K/A", "40-F/A", "20-F/A", "10-K/A")
SKIP_NAME = re.compile(r"(^R\d+\.htm$|^Financial_Report|\.xml$|\.xsd$|\.jpg$|\.png$|\.gif$|\.json$|\.zip$|-index|\.xlsx?$)", re.I)


class SecError(RuntimeError):
    pass


@dataclass
class FilingRef:
    accession: str
    form: str
    filed: date | None
    period: date | None
    primary_doc: str
    description: str = ""
    items: str = ""

    @property
    def nodash(self) -> str:
        return self.accession.replace("-", "")


@dataclass
class Doc:
    name: str
    kind: str
    pages: list[str]
    url: str


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


class Edgar:
    def __init__(self, user_agent: str | None = None, getter=None, min_interval: float = 0.12, sleep=time.sleep, clock=time.monotonic):
        self.ua = user_agent or os.getenv("SEC_USER_AGENT")
        self._getter = getter
        self.min_interval, self._sleep, self._clock = min_interval, sleep, clock
        self._last = 0.0
        self._tickers: dict | None = None
        if not self.ua and getter is None:
            raise SecError('Set SEC_USER_AGENT to your name and email, e.g. "Jane Doe jane@example.com" (the SEC requires it).')

    def get(self, url: str) -> bytes:
        wait = self.min_interval - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)
        last_err: Exception | None = None
        for attempt in range(4):
            self._last = self._clock()
            try:
                if self._getter:
                    return self._getter(url)
                req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                    return gzip.decompress(data) if r.headers.get("Content-Encoding") == "gzip" else data
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503, 504):
                    self._sleep(2 ** attempt * 1.5)
                    continue
                raise SecError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                self._sleep(2 ** attempt)
        raise SecError(f"could not fetch {url}: {last_err}")

    def json(self, url: str):
        return json.loads(self.get(url))

    # ---------------------------------------------------------------- identifiers and filings
    def tickers(self) -> dict[str, tuple[str, str]]:
        """ticker -> (cik 10-digit string, company name)."""
        if self._tickers is None:
            raw = self.json("https://www.sec.gov/files/company_tickers.json")
            self._tickers = {v["ticker"].upper(): (str(v["cik_str"]).zfill(10), v["title"]) for v in raw.values()}
        return self._tickers

    def cik_for_ticker(self, ticker: str) -> str | None:
        t = ticker.upper().split(".")[0]
        hit = self.tickers().get(t)
        return hit[0] if hit else None

    def submissions(self, cik: str) -> dict:
        return self.json(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")

    @staticmethod
    def parse_recent(sub: dict, forms=DEFAULT_FORMS, since: date | None = None) -> list[FilingRef]:
        rec = (sub.get("filings") or {}).get("recent") or {}
        n = len(rec.get("accessionNumber", []))
        col = lambda k: rec.get(k) or [""] * n
        out = []
        for acc, form, filed, period, prim, desc, items in zip(col("accessionNumber"), col("form"), col("filingDate"), col("reportDate"),
                                                               col("primaryDocument"), col("primaryDocDescription"), col("items")):
            if forms and form not in forms:
                continue
            fd = _d(filed)
            if since and fd and fd < since:
                continue
            out.append(FilingRef(acc, form, fd, _d(period), prim, desc or "", items or ""))
        return sorted(out, key=lambda r: (r.filed or date.min, r.accession))

    def recent_filings(self, cik: str, forms=DEFAULT_FORMS, since: date | None = None) -> list[FilingRef]:
        return self.parse_recent(self.submissions(cik), forms, since)

    def index_names(self, cik: str, accession: str) -> list[str]:
        j = self.json(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/index.json")
        return [i["name"] for i in (j.get("directory") or {}).get("item", []) if i.get("name")]

    def doc_url(self, cik: str, accession: str, name: str) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{name}"


# ---------------------------------------------------------------- HTML -> quotable pages
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "ul", "ol", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        style = (a.get("style") or "").lower()
        if tag in ("script", "style"):
            self.skip += 1
        if "page-break-before" in style and "always" in style or tag == "hr":
            self.out.append("\f")
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            text = re.sub(r"\s+", " ", "".join(self.cell)).strip()
            if text in (")", "%") and self.row:
                self.row[-1] += text
            elif text and text != "$":
                self.row.append(text)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.out.append(" | ".join(self.row) + "\n")
            self.row = None
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        (self.cell if self.cell is not None else self.out).append(data)


def html_to_pages(html: str, target_chars: int = 4000, max_chars: int = 8000) -> list[str]:
    p = _Text()
    p.feed(html)
    text = "".join(p.out).replace("\xa0", " ")
    raw_pages = [pg for pg in text.split("\f")]
    pages: list[str] = []
    for pg in raw_pages:
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in pg.splitlines()]
        lines = [ln for ln in lines if ln]
        buf, size = [], 0
        for ln in lines:
            if size + len(ln) > target_chars and buf:
                pages.append("\n".join(buf))
                buf, size = [], 0
            buf.append(ln)
            size += len(ln) + 1
        if buf:
            pages.append("\n".join(buf))
    return [x[:max_chars] for x in pages if x.strip()]


def pdf_bytes_to_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader
    return [(p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]


# ---------------------------------------------------------------- what is this document?
def classify(text: str) -> str | None:
    """Rule-based document type from the opening of the text. None means 'not worth reading' (cover pages, certifications...)."""
    if len(text) < 1500:
        return None
    head = text[:6000].lower()
    body = text[:40000].lower()
    if "technical report" in head and any(k in head for k in ("ni 43-101", "national instrument 43-101", "s-k 1300", "regulation s-k")):
        return "technical_report"
    if "annual information form" in head[:3000]:
        return "aif"
    if "management's discussion and analysis" in head or "management’s discussion and analysis" in head:
        return "mdna"
    if ("statements of financial position" in body or "balance sheets" in body or "statements of financial condition" in body) \
            and ("statements of cash flows" in body or "cash flows" in body):
        return "financials"
    if any(k in head for k in ("news release", "press release", "reports q", "announces", "reports fourth", "reports second",
                                "reports third", "reports first", "results")) and any(
            k in body for k in ("quarter", "year ended", "full year", "mineral reserve", "mineral resource", "production")):
        return "press_release"
    return None


def fetch_filing_documents(edgar: Edgar, cik: str, ref: FilingRef, max_docs: int = 12, max_bytes: int = 30_000_000) -> list[Doc]:
    names = [n for n in edgar.index_names(cik, ref.accession) if not SKIP_NAME.search(n) and n.lower().endswith((".htm", ".html", ".pdf", ".txt"))]
    # exhibits first (they hold the results releases, MD&A and statements), the cover form last
    names.sort(key=lambda n: (n == ref.primary_doc, n))
    docs: list[Doc] = []
    for name in names:
        if len(docs) >= max_docs:
            break
        data = edgar.get(edgar.doc_url(cik, ref.accession, name))
        if len(data) > max_bytes:
            continue
        try:
            pages = pdf_bytes_to_pages(data) if name.lower().endswith(".pdf") else html_to_pages(data.decode("utf-8", "replace"))
        except Exception:
            continue
        kind = classify("\n".join(pages[:12]))
        if kind:
            docs.append(Doc(name, kind, pages, edgar.doc_url(cik, ref.accession, name)))
    return docs
