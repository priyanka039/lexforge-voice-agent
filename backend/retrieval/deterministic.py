"""Deterministic retrieval resolver — raw fetch + ranked output (Phase 3)."""
from __future__ import annotations

import asyncio
from typing import Sequence

from ..runtime import VocabSnapshot
from .base import CompositeRetriever, PrecedentResult, PrecedentRetriever
from .scoring import rank_results


class DeterministicRetriever(PrecedentRetriever):
    """Wraps a composite retriever; backends return raw candidates, we rank."""

    name = "deterministic"

    def __init__(
        self,
        inner: CompositeRetriever,
        vocab: VocabSnapshot,
        *,
        timeout_s: float = 8.0,
    ):
        self.inner = inner
        self.vocab = vocab
        self.timeout_s = timeout_s

    async def search(
        self,
        query: str,
        limit: int = 5,
        issue_keywords: Sequence[str] | None = None,
    ) -> list[PrecedentResult]:
        raw_limit = max(limit * 3, 15)
        try:
            raw = await asyncio.wait_for(
                self.inner.search(query, limit=raw_limit),
                self.timeout_s,
            )
        except Exception:
            raw = []
        issues = list(issue_keywords or [])
        return rank_results(query, raw, issues, self.vocab, limit=limit)

    async def close(self) -> None:
        await self.inner.close()
