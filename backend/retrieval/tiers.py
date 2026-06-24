"""Trust tiers for deterministic retrieval ranking (Phase 3)."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

# Tier weight (higher = more trusted). Used as primary sort key after score.
TIER_SEED = 1
TIER_INDIANKANOON_API = 1
TIER_DDG_OFFICIAL = 2
TIER_WIKIPEDIA = 3
TIER_OTHER_WEB = 4

TIER_WEIGHTS: dict[int, int] = {
    TIER_SEED: 100,
    TIER_INDIANKANOON_API: 100,
    TIER_DDG_OFFICIAL: 80,
    TIER_WIKIPEDIA: 30,
    TIER_OTHER_WEB: 10,
}

TIER_BONUS: dict[int, int] = {
    TIER_SEED: 5,
    TIER_INDIANKANOON_API: 5,
    TIER_DDG_OFFICIAL: 3,
    TIER_WIKIPEDIA: 0,
    TIER_OTHER_WEB: 0,
}

_OFFICIAL_DOMAINS = (
    "indiankanoon.org",
    "sci.gov.in",
    "ecourts.gov.in",
    "main.sci.gov.in",
)


@dataclass(frozen=True)
class SourceTier:
    tier: int
    weight: int
    bonus: int
    verified: bool


def classify_source(source: str, url: str = "") -> SourceTier:
    src = (source or "").lower()
    host = urlparse(url or "").netloc.lower().removeprefix("www.")

    if src in {"seed", "seed_corpus"}:
        return SourceTier(TIER_SEED, TIER_WEIGHTS[TIER_SEED], TIER_BONUS[TIER_SEED], True)
    if src in {"indiankanoon", "indiankanoon_api"}:
        return SourceTier(TIER_INDIANKANOON_API, TIER_WEIGHTS[TIER_INDIANKANOON_API],
                          TIER_BONUS[TIER_INDIANKANOON_API], True)
    if src in {"duckduckgo", "web", "web_search"}:
        if any(d in host for d in _OFFICIAL_DOMAINS):
            return SourceTier(TIER_DDG_OFFICIAL, TIER_WEIGHTS[TIER_DDG_OFFICIAL],
                              TIER_BONUS[TIER_DDG_OFFICIAL], True)
        return SourceTier(TIER_OTHER_WEB, TIER_WEIGHTS[TIER_OTHER_WEB],
                          TIER_BONUS[TIER_OTHER_WEB], False)
    if src == "wikipedia":
        return SourceTier(TIER_WIKIPEDIA, TIER_WEIGHTS[TIER_WIKIPEDIA],
                          TIER_BONUS[TIER_WIKIPEDIA], False)
    if any(d in host for d in _OFFICIAL_DOMAINS):
        return SourceTier(TIER_DDG_OFFICIAL, TIER_WEIGHTS[TIER_DDG_OFFICIAL],
                          TIER_BONUS[TIER_DDG_OFFICIAL], True)
    return SourceTier(TIER_OTHER_WEB, TIER_WEIGHTS[TIER_OTHER_WEB],
                      TIER_BONUS[TIER_OTHER_WEB], False)
