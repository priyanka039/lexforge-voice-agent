"""Tests for ledger compression (Phase 5)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.ledger import ArgumentLedger, compress_ledger, recompute_confidence


def test_compression_dedupes_claims():
    ledger = ArgumentLedger()
    ledger.add_claim("issue_0", "Article 21 applies")
    ledger.add_claim("issue_0", "article 21 applies")
    ledger.add_claim("issue_0", "Separate claim with more detail in meta", meta={"ref": 1})
    final = compress_ledger(ledger)
    assert len(final.claims) == 2
    conf = recompute_confidence(final)
    assert conf.by_issue["global"] >= 0.0


def test_confidence_uses_final_ledger_only():
    ledger = ArgumentLedger()
    ledger.add_claim("i1", "Claim A")
    ledger.add_authority("i1", "Kesavananda")
    final = compress_ledger(ledger)
    c1 = recompute_confidence(final)
    c2 = recompute_confidence(final)
    assert c1.by_issue == c2.by_issue
