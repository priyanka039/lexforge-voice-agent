"""Retrieval package: precedent search + citation tooling."""
from __future__ import annotations

from ..config import Settings
from .base import CompositeRetriever, PrecedentResult, PrecedentRetriever
from .citations import Citation, extract_citations, normalize_citation, normalize_spoken_numbers
from .deterministic import DeterministicRetriever
from .duckduckgo import DuckDuckGoRetriever
from .indiankanoon import IndianKanoonRetriever
from .legal_vocab import detect_topics, stt_biasing_prompt
from .seed_corpus import SeedCorpusRetriever
from .websearch import IndianKanoonScrapeRetriever
from .wikipedia import WikipediaRetriever

__all__ = [
    "PrecedentResult",
    "PrecedentRetriever",
    "CompositeRetriever",
    "Citation",
    "extract_citations",
    "normalize_citation",
    "normalize_spoken_numbers",
    "detect_topics",
    "stt_biasing_prompt",
    "build_retriever",
    "DeterministicRetriever",
]


def build_retriever(settings: Settings, vocab=None) -> DeterministicRetriever | CompositeRetriever:
    """Assemble the composite retriever from configured backends.

    All defaults are FREE (no token): Wikipedia + DuckDuckGo + landmark seed
    corpus. The paid Indian Kanoon API is used only if a token is present; the
    public-page scraper is opt-in (robots-disallowed) and off by default.

    Trust order (earlier wins de-dupes): IK API > IK scrape > Wikipedia > web >
    seed corpus.
    """
    t = settings.retrieval_timeout_s
    backends: list[PrecedentRetriever] = []
    if settings.indiankanoon_api_token:
        backends.append(IndianKanoonRetriever(settings.indiankanoon_api_token, t))
    if settings.enable_indiankanoon_scrape:
        backends.append(IndianKanoonScrapeRetriever(t, enabled=True))
    if settings.enable_wikipedia:
        backends.append(WikipediaRetriever(t, enabled=True))
    if settings.enable_web_search:
        backends.append(DuckDuckGoRetriever(t, enabled=True))
    # Always include the seed corpus as a guaranteed fallback.
    backends.append(SeedCorpusRetriever())
    composite = CompositeRetriever(backends, timeout_s=t)
    if vocab is not None:
        return DeterministicRetriever(composite, vocab, timeout_s=t)
    return composite
