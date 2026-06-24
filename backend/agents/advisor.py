"""Advisor agent (formerly Weakness) — silent case analyst + coach."""
from __future__ import annotations

import json

from ..state import MootCourtState
from .base import Agent, AgentResult, Message
from .context import build_bounded_context
from .schema import AgentOutput, WeaknessOutput


_SYS = """You are a moot-court advisor analysing an advocate's argument for \
weaknesses: unsupported assertions, mis-stated authorities, logical gaps, and \
procedural missteps under Indian law. Return STRICT JSON: \
{"weaknesses": ["...", "..."]} with at most 3 concise items (max ~15 words each). \
No prose outside the JSON."""

_COACH_SYS = """You are a supportive but candid moot-court advisor. In at most \
two spoken sentences, tell the advocate the single most important weakness in \
their current argument and how to shore it up. Plain spoken English, no lists."""


class AdvisorAgent(Agent):
    name = "advisor"

    async def analyze(self, state: MootCourtState) -> AgentResult:
        raw = await self.llm.chat(
            [Message("system", _SYS),
             Message("user", build_bounded_context(state)
                     + "\n\nAdvocate's latest argument:\n" + state.last_advocate_text())],
            model=self.model, temperature=0.3, max_tokens=200,
        )
        weaknesses = _parse_weaknesses(raw)
        structured = WeaknessOutput(weaknesses=weaknesses).to_structured()
        return AgentResult(
            agent=self.name,
            data=structured,
            state_updates={"known_weaknesses": weaknesses} if weaknesses else {},
            structured=structured,
        )

    async def coach(self, state: MootCourtState) -> AgentResult:
        spoken = await self.llm.chat(
            [Message("system", _COACH_SYS),
             Message("user", build_bounded_context(state)
                     + "\n\nAdvocate's latest argument:\n" + state.last_advocate_text())],
            model=self.model, temperature=0.4, max_tokens=140,
        )
        out = AgentOutput(agent=self.name, spoken_text=spoken, structured={"mode": "coach"})
        return AgentResult(
            agent=self.name,
            spoken_text=spoken,
            structured=out.to_dict(),
        )


# Back-compat alias — effect id and orchestrator bg task still use "weakness"
WeaknessAgent = AdvisorAgent


def _parse_weaknesses(raw: str) -> list[str]:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
        items = data.get("weaknesses", [])
        return [str(x).strip() for x in items if str(x).strip()][:3]
    except Exception:
        return []
