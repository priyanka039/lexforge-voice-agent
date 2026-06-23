"""Precedent retrieval interfaces and the composite retriever.

Design goals:
  * Pluggable backends (Indian Kanoon API, web fetch, local seed corpus).
  * Never block the voice loop: everything is async with hard timeouts.
  * Always return *something* (seed corpus guarantees graceful degradation).
"""
from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PrecedentResult:
    title: str                      # e.g. "Kesavananda Bharati v. State of Kerala"
    citation: str = ""              # canonical citation string
    court: str = ""
    year: Optional[int] = None
    summary: str = ""               # short holding / what it stands for
    url: str = ""
    source: str = ""                # which backend produced it
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "citation": self.citation,
            "court": self.court,
            "year": self.year,
            "summary": self.summary,
            "url": self.url,
            "source": self.source,
            "score": self.score,
        }

    def short(self) -> str:
        bits = [self.title]
        if self.citation:
            bits.append(f"({self.citation})")
        line = " ".join(bits)
        if self.summary:
            line += f" — {self.summary}"
        return line


class PrecedentRetriever(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[PrecedentResult]:
        ...

    async def close(self) -> None:  # optional cleanup hook
        return None


class CompositeRetriever(PrecedentRetriever):
    """Fans a query out to all backends in parallel, merges + de-dupes.

    Ordering of `backends` is the trust order used for tie-breaking and as the
    de-dupe winner. Each backend is wrapped in its own timeout so one slow or
    failing source never stalls the others.
    """
    name = "composite"

    def __init__(self, backends: list[PrecedentRetriever], timeout_s: float = 8.0):
        self.backends = backends
        self.timeout_s = timeout_s

    async def _safe(self, backend: PrecedentRetriever, query: str, limit: int):
        try:
            return await asyncio.wait_for(backend.search(query, limit), self.timeout_s)
        except Exception:
            return []

    async def search(self, query: str, limit: int = 5) -> list[PrecedentResult]:
        results = await asyncio.gather(
            *(self._safe(b, query, limit) for b in self.backends)
        )
        merged: list[PrecedentResult] = []
        seen: dict[str, PrecedentResult] = {}
        # backends earlier in the list win de-dupes
        for backend_results in results:
            for r in backend_results:
                key = _dedupe_key(r)
                if key in seen:
                    # keep the richer record (prefer one with a summary/citation)
                    existing = seen[key]
                    if len(r.summary) > len(existing.summary) and not existing.citation:
                        existing.summary = r.summary or existing.summary
                    continue
                seen[key] = r
                merged.append(r)
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:limit]

    async def close(self) -> None:
        await asyncio.gather(*(b.close() for b in self.backends), return_exceptions=True)


def _dedupe_key(r: PrecedentResult) -> str:
    base = (r.citation or r.title).lower()
    # normalise the most common name noise
    base = base.replace("v.", "v").replace("&", "and")
    return "".join(ch for ch in base if ch.isalnum())[:60]
