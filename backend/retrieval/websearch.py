"""Indian Kanoon public-page retriever — OPT-IN, OFF BY DEFAULT.

Indian Kanoon's official API is paid. Its public search page works over a GET
request, BUT `https://indiankanoon.org/robots.txt` contains `Disallow: /search/`.
To respect that, this backend is **disabled by default** and only runs when you
explicitly set `ENABLE_INDIANKANOON_SCRAPE=true`, having decided that's
acceptable for your use. For free retrieval you generally don't need it: the
Wikipedia and DuckDuckGo backends already surface Indian case law (including
links to Indian Kanoon document pages) without touching the disallowed path.

Any failure returns [] so the composite retriever degrades gracefully.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from .base import PrecedentResult, PrecedentRetriever
from .citations import extract_citations

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LexForgeMootCourt/1.0"
)


class IndianKanoonScrapeRetriever(PrecedentRetriever):
    name = "indiankanoon_scrape"

    def __init__(self, timeout_s: float = 8.0, enabled: bool = False):
        self.timeout_s = timeout_s
        self.enabled = enabled
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9"},
                follow_redirects=True,
            )
        return self._client

    async def search(self, query: str, limit: int = 5) -> list[PrecedentResult]:
        if not self.enabled or not query.strip():
            return []
        url = f"https://indiankanoon.org/search/?formInput={quote_plus(query)}"
        try:
            resp = await self._http().get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            return []

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        out: list[PrecedentResult] = []
        for i, result in enumerate(soup.select("div.result")[:limit]):
            title_el = result.select_one(".result_title a") or result.select_one("a")
            if not title_el:
                continue
            title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True))
            href = title_el.get("href", "")
            if href.startswith("/"):
                href = "https://indiankanoon.org" + href
            snippet_el = result.select_one(".snippet") or result.select_one(".fragment")
            summary = re.sub(r"\s+", " ", snippet_el.get_text(" ", strip=True)) if snippet_el else ""
            court_el = result.select_one(".docsource") or result.select_one(".cite_tag")
            court = court_el.get_text(strip=True) if court_el else ""
            cites = extract_citations(title + " " + summary)
            citation = cites[0].canonical if cites else ""
            year = None
            m = re.search(r"\b(19|20)\d{2}\b", title + " " + summary)
            if m:
                year = int(m.group(0))
            out.append(PrecedentResult(
                title=title, citation=citation, court=court, year=year,
                summary=summary[:280], url=href, source=self.name, score=9.0 - i * 0.5,
            ))
        return out

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
