"""Tests for language middleware (Phase 6)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.language import build_language_instruction, validate_and_rewrite_spoken
from backend.state import LanguageSettings


def test_english_instruction():
    s = LanguageSettings()
    assert build_language_instruction(s) == "Respond in English."


def test_hindi_instruction():
    s = LanguageSettings(spoken_language="hi")
    text = build_language_instruction(s)
    assert "Hindi" in text
    assert "citations" in text.lower() or "English" in text


def test_validate_english_strips_whitespace():
    out = validate_and_rewrite_spoken("  Hello   world  ", LanguageSettings())
    assert out == "Hello world"
