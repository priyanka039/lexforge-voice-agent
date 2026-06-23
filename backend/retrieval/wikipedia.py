"""Wikipedia retriever — free, no API key, no token.

Wikipedia has high-quality, well-sourced articles on virtually every landmark
Indian judgment (Kesavananda Bharati, Maneka Gandhi, Puttaswamy, Navtej Johar,
Indira Gandhi v. Raj Narain, ...). We use the public MediaWiki search API to
find candidates and the REST summary API to pull a clean holding-style extract.

Wikimedia's policy asks for a descriptive User-Agent; we send one. This is an
officially supported, free, rate-friendly API.
"""
from __future__ import annotations

import asyncio
import re

import httpx

from .base import PrecedentResult, PrecedentRetriever
from .citations import extract_citations

_WIKI_UA = "LexForgeMootCourt/1.0 (educational moot-court tool; +https://lexforge.local)"
_API = "https://en.wikipedia.org/w/api.php"
_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"

_LAW_HINTS = ("court", "judgment", "judgement", "petition", "constitution",
              "article", "appellant", "respondent", "held", "bench", "supreme court",
              "high court", "section", "act ")


class WikipediaRetriever(PrecedentRetriever):
    name = "wikipedia"

    def __init__(self, timeout_s: float = 8.0, enabled: bool = True):
        self.timeout_s = timeout_s
        self.enabled = enabled
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"User-Agent": _WIKI_UA, "Accept": "application/json"},
                follow_redirects=True,
            )
        return self._client

    async def search(self, query: str, limit: int = 5) -> list[PrecedentResult]:
        if not self.enabled or not query.strip():
            return []
        # Bias the search toward Indian case law.
        srquery = f"{query} India Supreme Court case judgment"
        try:
            resp = await self._http().get(_API, params={
                "action": "query", "list": "search", "srsearch": srquery,
                "format": "json", "srlimit": min(limit + 3, 10),
            })
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search", [])
        except Exception:
            return []

        titles = [h["title"] for h in hits][: limit + 3]
        summaries = await asyncio.gather(*(self._summary(t) for t in titles))
        out: list[PrecedentResult] = []
        for r in summaries:
            if r is not None:
                out.append(r)
        out.sort(key=lambda r: r.score, reverse=True)
        return out[:limit]

    async def _summary(self, title: str) -> PrecedentResult | None:
        try:
            resp = await self._http().get(_SUMMARY + title.replace(" ", "_"))
            if resp.status_code != 200:
                return None
            d = resp.json()
        except Exception:
            return None
        extract = (d.get("extract") or "").strip()
        disp = d.get("title") or title
        low = (disp + " " + extract).lower()
        is_case = " v. " in disp or " v " in disp.lower() or " vs " in low
        # filter out clearly non-legal pages
        if not is_case and not any(h in low for h in _LAW_HINTS):
            return None
        cites = extract_citations(extract)
        citation = cites[0].canonical if cites else ""
        year = None
        m = re.search(r"\b(18|19|20)\d{2}\b", extract)
        if m:
            year = int(m.group(0))
        url = d.get("content_urls", {}).get("desktop", {}).get("page", "")
        score = 6.0
        if is_case:
            score += 3.0
        if citation:
            score += 1.0
        return PrecedentResult(
            title=disp,
            citation=citation,
            court="" ,
            year=year,
            summary=extract[:300],
            url=url,
            source=self.name,
            score=score,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
