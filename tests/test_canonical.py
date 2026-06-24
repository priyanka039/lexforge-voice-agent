"""Tests for canonical serialization (R3)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.canonical import CANONICAL_SPEC_VERSION, canonical_dumps, digest, normalize_string


def test_canonical_spec_version_in_digest():
    d1 = digest({"a": 1})
    d2 = digest({"a": 1})
    assert d1 == d2
    assert CANONICAL_SPEC_VERSION == "1.0"


def test_nfkc_normalization():
    assert normalize_string("  hello   world  ") == "hello world"


def test_float_round_half_even():
    assert digest({"score": 1.005}) == digest({"score": "1.005000"})


def test_canonical_dumps_stable():
    payload = {"b": 2, "a": 1, "nested": {"z": 1, "y": 2}}
    assert digest(payload) == digest(payload)
    assert "canonical_spec_version" in canonical_dumps(payload)
