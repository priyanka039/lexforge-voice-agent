"""Weakness agent — silent analyst of the advocate's case.

Runs in the background after each advocate turn, maintaining a rolling list of
vulnerabilities in their position. It rarely speaks; instead it feeds the Judge
and Counter agents so their questions land where it hurts. When the advocate
explicitly asks for coaching, it can voice its top finding.
"""
from __future__ import annotations

import json

from ..state import MootCourtState
from .base import Agent, AgentResult, Message

_SYS = """You are a moot-court coach analysing an advocate's argument for \
weaknesses: unsupported assertions, mis-stated or distinguishable authorities, \
logical gaps, ignored counter-principles, and procedural missteps under Indian \
law. Return STRICT JSON: {"weaknesses": ["...", "..."]} with at most 3 concise \
items (max ~15 words each). No prose outside the JSON."""

_COACH_SYS = """You are a supportive but candid moot-court coach. In at most \
two spoken sentences, tell the advocate the single most important weakness in \
their current argument and how to shore it up. Plain spoken English, no lists."""


class WeaknessAgent(Agent):
    name = "weakness"

    async def analyze(self, state: MootCourtState) -> AgentResult:
        """Background: update the shared weakness list (does not speak)."""
        raw = await self.llm.chat(
            [Message("system", _SYS),
             Message("user", "Case context:\n" + state.context_block()
                     + "\n\nAdvocate's latest argument:\n" + state.last_advocate_text())],
            model=self.model, temperature=0.3, max_tokens=200,
        )
        weaknesses = _parse_weaknesses(raw)
        return AgentResult(
            agent=self.name,
            data={"weaknesses": weaknesses},
            state_updates={"known_weaknesses": weaknesses} if weaknesses else {},
        )

    async def coach(self, state: MootCourtState) -> AgentResult:
        """Foreground: speak the top weakness when the advocate asks for help."""
        spoken = await self.llm.chat(
            [Message("system", _COACH_SYS),
             Message("user", "Case context:\n" + state.context_block()
                     + "\n\nKnown weaknesses: " + "; ".join(state.known_weaknesses[-3:])
                     + "\n\nAdvocate's latest argument:\n" + state.last_advocate_text())],
            model=self.model, temperature=0.4, max_tokens=140,
        )
        return AgentResult(agent=self.name, spoken_text=spoken)


def _parse_weaknesses(raw: str) -> list[str]:
    raw = raw.strip()
    # tolerate code fences or stray text around the JSON
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
        items = data.get("weaknesses", [])
        return [str(x).strip() for x in items if str(x).strip()][:3]
    except Exception:
        return []
