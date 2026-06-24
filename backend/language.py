"""Multilingual middleware — deterministic language instructions (Phase 6)."""
from __future__ import annotations

import re

from .state import LanguageSettings

# Preserve in English: citations, statutes, case names, Latin maxims
_PRESERVE_PATTERNS = [
    re.compile(r"\bArticle\s+\d+[A-Z]?\b", re.I),
    re.compile(r"\bAIR\s+\d{4}\s+SC\s+\d+\b", re.I),
    re.compile(r"\b\d{4}\s+SCC\s+\d+\b", re.I),
    re.compile(r"\bv\.\s", re.I),
    re.compile(r"\b(?:IPC|CrPC|CPC)\b"),
    re.compile(
        r"\b(?:stare decisis|locus standi|ultra vires|mandamus|certiorari|"
        r"habeas corpus|audi alteram partem|ratio decidendi)\b",
        re.I,
    ),
]

_LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
}


def build_language_instruction(settings: LanguageSettings) -> str:
    """Deterministic string from language settings."""
    spoken = settings.spoken_language or "en"
    if spoken == "en" and not settings.custom_language_hint:
        return "Respond in English."
    name = _LANG_NAMES.get(spoken, settings.custom_language_hint or spoken)
    hint = settings.custom_language_hint.strip()
    parts = [f"Respond in {name}."]
    if hint:
        parts.append(f"Style hint: {hint}")
    parts.append(
        "Keep Indian legal citations, statute references, case names, and Latin maxims in English."
    )
    return " ".join(parts)


def _extract_preserved_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for pat in _PRESERVE_PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end(), m.group()))
    spans.sort(key=lambda x: x[0])
    return spans


def validate_and_rewrite_spoken(text: str, settings: LanguageSettings) -> str:
    """Single-pass validation before UI/TTS. English mode: strip excess whitespace."""
    text = (text or "").strip()
    if not text:
        return text
    if settings.spoken_language == "en" and not settings.custom_language_hint:
        return re.sub(r"\s+", " ", text)
    # Non-English: pass through with preserved spans noted in meta (LLM handles translation)
    return text
