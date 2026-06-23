"""Indian legal citation normalisation.

Speech-to-text mangles citations badly. A lawyer says
"AIR nineteen seventy-three SC twenty-three sixty-nine" and Whisper/Gemini may
emit "air 1973 sc 23 69" or "a i r 1973 s c 2369". This module:

  1. Repairs spoken-number artefacts and spacing.
  2. Detects the common Indian reporter formats (AIR, SCC, SCR, SCALE, ...).
  3. Produces a canonical citation string + structured parts for verification.

Supported formats (non-exhaustive, easy to extend):
  AIR 1973 SC 2369
  (1973) 4 SCC 225
  1973 SCR (3) 757
  (2017) 10 SCC 1
  AIR 2018 SC 357
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Reporter abbreviations we recognise. Map noisy variants -> canonical.
_REPORTERS = {
    "AIR": ["air", "a i r", "a.i.r", "a i r."],
    "SCC": ["scc", "s c c", "s.c.c"],
    "SCR": ["scr", "s c r", "s.c.r"],
    "SCALE": ["scale"],
    "SCJ": ["scj", "s c j"],
    "AIRSC": ["airsc"],
    "MANU": ["manu"],
}

# Courts that appear inside AIR-style citations.
_COURTS = {
    "SC": ["sc", "s c", "s.c", "supreme court"],
    "SCC": ["scc"],
    "DEL": ["del", "delhi"],
    "BOM": ["bom", "bombay"],
    "MAD": ["mad", "madras"],
    "CAL": ["cal", "calcutta"],
    "ALL": ["all", "allahabad"],
    "KAR": ["kar", "karnataka"],
    "KER": ["ker", "kerala"],
}

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
}


@dataclass
class Citation:
    raw: str
    canonical: str
    reporter: Optional[str] = None
    year: Optional[int] = None
    court: Optional[str] = None
    volume: Optional[str] = None
    page: Optional[str] = None
    confidence: float = 0.5
    parts: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.canonical


_TENS = {20, 30, 40, 50, 60, 70, 80, 90}


def _additive_value(tokens: list[str]) -> Optional[int]:
    """Standard additive evaluation, e.g. 'two thousand seventeen' -> 2017."""
    total = 0
    current = 0
    seen = False
    for tok in tokens:
        if tok == "and":
            continue
        val = _NUM_WORDS[tok]
        seen = True
        if val == 100:
            current = (current or 1) * 100
        elif val == 1000:
            total += (current or 1) * 1000
            current = 0
        else:
            current += val
    return (total + current) if seen else None


def _group_components(tokens: list[str]) -> list[int]:
    """Group bare number words into sub-numbers, combining tens+unit.

    'nineteen seventy three' -> [19, 73]; 'twenty three sixty nine' -> [23, 69].
    """
    comps: list[int] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "and":
            i += 1
            continue
        v = _NUM_WORDS[tokens[i]]
        if v in _TENS and i + 1 < len(tokens) and tokens[i + 1] != "and" \
                and 1 <= _NUM_WORDS.get(tokens[i + 1], 0) <= 9:
            comps.append(v + _NUM_WORDS[tokens[i + 1]])
            i += 2
        else:
            comps.append(v)
            i += 1
    return comps


def _words_to_number(phrase: str) -> Optional[int]:
    """Convert a spoken number phrase to an int.

    Courtroom citations are usually read as concatenated two-digit groups
    ('nineteen seventy-three' = 1973, 'twenty-three sixty-nine' = 2369). When a
    scale word (hundred/thousand) appears we fall back to additive parsing
    ('two thousand seventeen' = 2017).
    """
    tokens = re.findall(r"[a-z]+", phrase.lower())
    tokens = [t for t in tokens if t in _NUM_WORDS or t == "and"]
    if not tokens:
        return None
    if "hundred" in tokens or "thousand" in tokens:
        return _additive_value(tokens)
    comps = _group_components(tokens)
    if not comps:
        return None
    out = str(comps[0])
    for c in comps[1:]:
        out += f"{c:02d}"
    try:
        return int(out)
    except ValueError:
        return None


# Build an alternation that prefers the longest words (so 'seventy' wins over
# 'seven') and anchors on word boundaries to avoid clobbering prose.
_NUM_ALTERNATION = "|".join(
    sorted(list(_NUM_WORDS.keys()) + ["and"], key=len, reverse=True)
)
_NUM_RUN_RX = re.compile(
    rf"\b((?:(?:{_NUM_ALTERNATION})\b\s*){{2,}})",
    re.IGNORECASE,
)


def normalize_spoken_numbers(text: str) -> str:
    """Best-effort repair of spoken number runs that STT left as words.

    We only collapse *runs* of number-words so we don't clobber ordinary prose.
    'nineteen seventy three' -> '1973'. Conservative on short runs.
    """
    def _repl(m: re.Match) -> str:
        phrase = m.group(1)
        n = _words_to_number(phrase)
        return f" {n} " if n is not None else m.group(0)

    return _NUM_RUN_RX.sub(_repl, text)


def _clean(text: str) -> str:
    """Collapse spaced-out acronyms and tidy whitespace."""
    t = text
    # Join single-letter sequences: "a i r" -> "air", "s c c" -> "scc".
    t = re.sub(r"\b([a-zA-Z])(?:\s+([a-zA-Z])){1,3}\b",
               lambda m: m.group(0).replace(" ", ""), t)
    t = re.sub(r"\.", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _canon_reporter(token: str) -> Optional[str]:
    tok = token.lower().replace(" ", "").replace(".", "")
    for canon, variants in _REPORTERS.items():
        for v in variants:
            if tok == v.replace(" ", "").replace(".", ""):
                return canon
    return None


def _canon_court(token: str) -> Optional[str]:
    tok = token.lower().replace(" ", "").replace(".", "")
    for canon, variants in _COURTS.items():
        for v in variants:
            if tok == v.replace(" ", "").replace(".", ""):
                return canon
    return None


# Regexes run against the cleaned text.
_RX_AIR = re.compile(r"\b(air)\s*(\d{4})\s*([a-z]{2,3})\s*(\d{1,5})\b", re.IGNORECASE)
_RX_SCC = re.compile(r"\(?\s*(\d{4})\s*\)?\s*(\d{1,2})\s*(scc|scr|scale)\s*(\d{1,5})\b", re.IGNORECASE)
_RX_SCR_VOL = re.compile(r"\b(\d{4})\s*(scr|scc)\s*\(?\s*(\d{1,2})\s*\)?\s*(\d{1,5})\b", re.IGNORECASE)


def extract_citations(text: str) -> list[Citation]:
    """Find and normalise any citations in a (possibly noisy) string."""
    repaired = normalize_spoken_numbers(text)
    cleaned = _clean(repaired)
    found: list[Citation] = []
    seen: set[str] = set()

    def _add(c: Citation) -> None:
        if c.canonical not in seen:
            seen.add(c.canonical)
            found.append(c)

    for m in _RX_AIR.finditer(cleaned):
        reporter = _canon_reporter(m.group(1)) or "AIR"
        year = int(m.group(2))
        court = _canon_court(m.group(3)) or m.group(3).upper()
        page = m.group(4)
        canonical = f"{reporter} {year} {court} {page}"
        _add(Citation(raw=m.group(0), canonical=canonical, reporter=reporter,
                      year=year, court=court, page=page, confidence=0.85))

    for m in _RX_SCC.finditer(cleaned):
        year = int(m.group(1))
        vol = m.group(2)
        reporter = m.group(3).upper()
        page = m.group(4)
        canonical = f"({year}) {vol} {reporter} {page}"
        _add(Citation(raw=m.group(0), canonical=canonical, reporter=reporter,
                      year=year, volume=vol, page=page, confidence=0.8))

    for m in _RX_SCR_VOL.finditer(cleaned):
        year = int(m.group(1))
        reporter = m.group(2).upper()
        vol = m.group(3)
        page = m.group(4)
        canonical = f"{year} {reporter} ({vol}) {page}"
        _add(Citation(raw=m.group(0), canonical=canonical, reporter=reporter,
                      year=year, volume=vol, page=page, confidence=0.75))

    return found


def normalize_citation(text: str) -> Optional[Citation]:
    """Return the single best citation found in `text`, if any."""
    cites = extract_citations(text)
    if not cites:
        return None
    return max(cites, key=lambda c: c.confidence)
