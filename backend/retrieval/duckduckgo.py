"""DuckDuckGo retriever — free web search, no API key.

Uses DuckDuckGo's HTML endpoint to discover Indian case law across the open web
(Indian Kanoon document pages, the Supreme Court / eCourts judgment portals,
law reports and commentaries). This is link discovery, not scraping a protected
search index, and needs no token.

Defensive: any failure returns [] so the composite retriever degrades to
Wikipedia / the seed corpus.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

from .base import PrecedentResult, PrecedentRetriever
from .citations import extract_citations

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_ENDPOINTS = ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"]

# Domains we trust more for Indian case law (higher score).
_TRUSTED = {
    "indiankanoon.org": 3.0,
    "main.sci.gov.in": 3.0,
    "sci.gov.in": 3.0,
    "judgments.ecourts.gov.in": 2.5,
    "ecourts.gov.in": 2.0,
    "scconline": 2.0,
    "barandbench.com": 1.0,
    "livelaw.in": 1.0,
}


def _domain_bonus(url: str) -> float:
    for d, b in _TRUSTED.items():
        if d in url:
            return b
    return 0.0


class DuckDuckGoRetriever(PrecedentRetriever):
    name = "web"

    def __init__(self, timeout_s: float = 8.0, enabled: bool = True):
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
        q = f"{query} Indian case law judgment (indiankanoon OR supreme court of india)"
        html = ""
        for url in _ENDPOINTS:
            try:
                resp = await self._http().post(url, data={"q": q})
                resp.raise_for_status()
                html = resp.text
                if html:
                    break
            except Exception:
                continue
        if not html:
            return []

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        out: list[PrecedentResult] = []
        seen: set[str] = set()
        anchors = soup.select("a.result__a") or soup.select("a.result-link") or soup.select("a")
        rank = 0
        for a in anchors:
            href = a.get("href", "")
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = unquote(m.group(1))
            if not href.startswith("http"):
                continue
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            if not title or href in seen:
                continue
            seen.add(href)
            # snippet: nearest result body
            snip_el = None
            parent = a.find_parent(class_=re.compile("result"))
            if parent:
                snip_el = parent.select_one(".result__snippet") or parent.select_one(".result-snippet")
            summary = re.sub(r"\s+", " ", snip_el.get_text(" ", strip=True)) if snip_el else ""
            cites = extract_citations(title + " " + summary)
            citation = cites[0].canonical if cites else ""
            year = None
            ym = re.search(r"\b(19|20)\d{2}\b", title + " " + summary)
            if ym:
                year = int(ym.group(0))
            rank += 1
            score = max(0.5, 5.0 - rank * 0.4) + _domain_bonus(href)
            out.append(PrecedentResult(
                title=title[:160], citation=citation, year=year,
                summary=summary[:280], url=href, source=self.name, score=score,
            ))
            if len(out) >= limit + 4:
                break
        out.sort(key=lambda r: r.score, reverse=True)
        return out[:limit]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
