"""Deterministic precedent scoring (Phase 3, R11)."""
from __future__ import annotations

import re
from typing import Any, Sequence

from ..canonical import normalize_string
from ..runtime import VocabSnapshot
from .base import PrecedentResult
from .tiers import SourceTier, classify_source

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "is", "was",
    "are", "with", "by", "as", "at", "from", "that", "this", "be", "it",
})

_TOKEN_RE = re.compile(r"[^\w\s]", re.UNICODE)


def tokenize(text: str) -> tuple[str, ...]:
    text = normalize_string(text).lower()
    text = _TOKEN_RE.sub(" ", text)
    tokens = tuple(t for t in text.split() if t and t not in _STOPWORDS)
    return tokens


def _overlap_score(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(len(sa | sb), 1)


def legal_term_overlap(query_tokens: Sequence[str], vocab: VocabSnapshot) -> float:
    vocab_tokens: set[str] = set()
    for term in vocab.terms:
        for tok in tokenize(term):
            if len(tok) > 2:
                vocab_tokens.add(tok)
    if not vocab_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in vocab_tokens)
    return min(1.0, hits / max(len(query_tokens), 1))


def score_result(
    query: str,
    result: PrecedentResult,
    issue_keywords: Sequence[str],
    vocab: VocabSnapshot,
) -> tuple[float, SourceTier]:
    """Deterministic score — same inputs always produce same output."""
    q_tokens = tokenize(query)
    title_tokens = tokenize(result.title)
    summary_tokens = tokenize(result.summary or "")
    issue_tokens = tuple(tok for kw in issue_keywords for tok in tokenize(kw))

    query_overlap = _overlap_score(q_tokens, title_tokens + summary_tokens)
    issue_overlap = _overlap_score(issue_tokens, title_tokens + summary_tokens) if issue_tokens else 0.0
    term_overlap = legal_term_overlap(q_tokens + issue_tokens, vocab)

    citation_match = 0.0
    if result.citation:
        cit_tokens = tokenize(result.citation)
        citation_match = _overlap_score(q_tokens, cit_tokens)

    tier = classify_source(result.source, result.url)
    raw = (
        query_overlap * 40.0
        + issue_overlap * 25.0
        + term_overlap * 20.0
        + citation_match * 10.0
        + tier.bonus
    )
    # Quantize to 6 decimal places for cross-run stability
    score = round(raw, 6)
    return score, tier


def sort_key_for_result(
    score: float,
    tier: SourceTier,
    result: PrecedentResult,
) -> tuple:
    """Stable tie-break: score desc, tier asc, citation lex, title lex, url lex."""
    cit = normalize_string(result.citation or "").lower()
    title = normalize_string(result.title or "").lower()
    url = normalize_string(result.url or "").lower()
    exact_citation = 0 if result.citation else 1
    return (-score, tier.tier, exact_citation, cit, title, url)


def rank_results(
    query: str,
    results: list[PrecedentResult],
    issue_keywords: Sequence[str],
    vocab: VocabSnapshot,
    *,
    limit: int = 8,
) -> list[PrecedentResult]:
    scored: list[tuple[PrecedentResult, float, SourceTier]] = []
    for r in results:
        score, tier = score_result(query, r, issue_keywords, vocab)
        r.score = score
        if not tier.verified:
            r.source = f"{r.source}:unverified" if r.source else "unverified"
        scored.append((r, score, tier))
    scored.sort(key=lambda item: sort_key_for_result(item[1], item[2], item[0]))
    return [r for r, _, _ in scored[:limit]]
