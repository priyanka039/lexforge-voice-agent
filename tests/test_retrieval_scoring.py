"""Tests for deterministic retrieval scoring (Phase 3)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.retrieval.base import PrecedentResult
from backend.retrieval.scoring import rank_results, score_result
from backend.retrieval.tiers import TIER_SEED
from backend.runtime import VocabSnapshot


def _vocab() -> VocabSnapshot:
    return VocabSnapshot.from_terms("1.0.0", ("fundamental rights", "Article 21"))


def test_scoring_deterministic():
    vocab = _vocab()
    r = PrecedentResult(
        title="Maneka Gandhi v. Union of India",
        citation="AIR 1978 SC 597",
        summary="Expanded Article 21 due process",
        source="seed",
    )
    s1, _ = score_result("Article 21 due process", r, ["Article 21"], vocab)
    s2, _ = score_result("Article 21 due process", r, ["Article 21"], vocab)
    assert s1 == s2


def test_rank_order_stable():
    vocab = _vocab()
    query = "basic structure doctrine"
    results = [
        PrecedentResult(title="Random Web Case", source="web", url="https://example.com/x"),
        PrecedentResult(
            title="Kesavananda Bharati v. State of Kerala",
            citation="AIR 1973 SC 1461",
            summary="Basic structure doctrine",
            source="seed",
        ),
    ]
    ranked = rank_results(query, results, ["basic structure"], vocab, limit=2)
    assert ranked[0].title.startswith("Kesavananda")


def test_unverified_label_for_wikipedia():
    vocab = _vocab()
    r = PrecedentResult(title="Some case", source="wikipedia", summary="test")
    _, tier = score_result("test", r, [], vocab)
    ranked = rank_results("test", [r], [], vocab, limit=1)
    assert "unverified" in ranked[0].source or not tier.verified
