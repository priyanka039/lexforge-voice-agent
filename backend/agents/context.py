"""Bounded deterministic context windows for agents (Phase 4)."""
from __future__ import annotations

from typing import Any

from ..ledger import ArgumentLedger
from ..state import MootCourtState

MAX_DIALOGUE_TURNS = 6
MAX_AUTHORITIES = 8
MAX_CLAIMS = 5


def build_bounded_context(state: MootCourtState, ledger: ArgumentLedger | None = None) -> str:
    """Fixed-size context block — same state always yields same string."""
    lines = [state.brief.summary()]
    lines.append(f"Practice mode: {state.practice_mode.value}.")
    lines.append(f"Bench temperament: {state.bench_temperament.value}.")
    if state.language.spoken_language != "en":
        lines.append(f"Spoken language: {state.language.spoken_language}.")
    if state.open_judge_question:
        lines.append(f"Pending bench question: {state.open_judge_question}")

    ledger = ledger or state.ledger
    if ledger:
        claims = ledger.active_entries("claims")[-MAX_CLAIMS:]
        if claims:
            lines.append("Active claims: " + "; ".join(c.text for c in claims))
        authorities = ledger.active_entries("authorities")[-MAX_AUTHORITIES:]
        if authorities:
            lines.append("Authorities in ledger: " + "; ".join(a.text for a in authorities))

    if state.cited_cases:
        lines.append("Cited: " + ", ".join(state.cited_cases[-MAX_AUTHORITIES:]))
    if state.discussed_topics:
        lines.append("Topics: " + ", ".join(state.discussed_topics[-6:]))
    if state.known_weaknesses:
        lines.append("Known weaknesses: " + "; ".join(state.known_weaknesses[-3:]))

    lines.append("\nRecent exchange:\n" + state.recent_dialogue(MAX_DIALOGUE_TURNS))
    return "\n".join(lines)
