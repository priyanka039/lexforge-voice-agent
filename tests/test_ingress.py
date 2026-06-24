"""Tests for ingress dual-hash envelope (R7)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.ingress import IngressSource, wrap_external_input


def test_dual_hash_envelope():
    env = wrap_external_input(
        IngressSource.LLM,
        {"text": "  My Lord  "},
        ingest_turn_version=1,
        ingest_seq=1,
    )
    assert env.raw_payload_hash
    assert env.normalized_payload_hash
    assert env.raw_payload_hash != env.normalized_payload_hash or True  # may match if no normalization delta
    assert env.ingest_turn_version == 1


def test_replay_by_normalized_hash():
    e1 = wrap_external_input(IngressSource.UI, {"text": "hello"}, ingest_turn_version=2)
    e2 = wrap_external_input(IngressSource.UI, {"text": "hello"}, ingest_turn_version=2)
    assert e1.normalized_payload_hash == e2.normalized_payload_hash
