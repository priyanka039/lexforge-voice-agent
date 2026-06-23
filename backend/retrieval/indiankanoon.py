"""Indian Kanoon official API adapter.

Indian Kanoon (indiankanoon.org) exposes a token-authenticated HTTP API which
is the most reliable programmatic source of Indian case law (Supreme Court,
all High Courts, tribunals, and bare acts).

  Endpoint:  POST https://api.indiankanoon.org/search/?formInput=<q>&pagenum=0
  Auth:      Authorization: Token <INDIANKANOON_API_TOKEN>

If no token is configured this adapter disables itself (returns []), so the
CompositeRetriever falls back to the web/seed backends transparently.

API docs: https://api.indiankanoon.org/
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote_plus

import httpx

from .base import PrecedentResult, PrecedentRetriever

_API = "https://api.indiankanoon.org"


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


class IndianKanoonRetriever(PrecedentRetriever):
    name = "indiankanoon"

    def __init__(self, token: str, timeout_s: float = 8.0):
        self.token = token
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={
                    "Authorization": f"Token {self.token}",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def search(self, query: str, limit: int = 5) -> list[PrecedentResult]:
        if not self.enabled:
            return []
        # Bias toward reported judgments, not random documents.
        url = f"{_API}/search/?formInput={quote_plus(query)}&pagenum=0"
        try:
            resp = await self._http().post(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        docs = data.get("docs", []) or []
        out: list[PrecedentResult] = []
        for i, d in enumerate(docs[:limit]):
            title = _strip_html(d.get("title", "")) or "Untitled"
            headline = _strip_html(d.get("headline", ""))
            docsource = d.get("docsource", "") or ""
            doc_id = d.get("tid") or d.get("docid")
            year = None
            m = re.search(r"\b(19|20)\d{2}\b", title + " " + headline)
            if m:
                year = int(m.group(0))
            out.append(PrecedentResult(
                title=title,
                citation=_strip_html(d.get("citation", "")),
                court=docsource,
                year=year,
                summary=headline[:280],
                url=f"https://indiankanoon.org/doc/{doc_id}/" if doc_id else "",
                source=self.name,
                # rank by API order (earlier = more relevant)
                score=10.0 - i * 0.5,
            ))
        return out

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
