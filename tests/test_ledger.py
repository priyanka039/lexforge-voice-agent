"""Tests for ArgumentLedger (Phase 2)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.ledger import (
    ArgumentLedger,
    FinalLedger,
    apply_agent_output_to_ledger,
    recompute_confidence,
    sync_ledger_from_state,
)
from backend.state import MootCourtState


def test_ledger_digest_stable():
    ledger = ArgumentLedger()
    ledger.add_claim("issue_0", "Article 21 applies")
    d1 = ledger.digest()
    d2 = ledger.digest()
    assert d1 == d2


def test_sync_and_confidence_from_state():
    state = MootCourtState()
    state.brief.issues = ["Whether Article 21 applies"]
    state.known_weaknesses = ["Weak factual foundation"]
    state.cited_cases = ["Kesavananda Bharati v. State of Kerala"]
    sync_ledger_from_state(state)
    assert state.ledger is not None
    assert len(state.ledger.claims) >= 1
    assert len(state.ledger.weaknesses) >= 1
    final = FinalLedger.from_ledger(state.ledger)
    conf = recompute_confidence(final)
    assert "global" in conf.by_issue


def test_apply_agent_precedents():
    state = MootCourtState()
    apply_agent_output_to_ledger(state, "precedent", {
        "precedents": [{"title": "Maneka Gandhi v. Union of India", "citation": "AIR 1978 SC 597"}],
    })
    assert state.ledger is not None
    assert any("Maneka" in e.text for e in state.ledger.authorities)
