"""Judge agent — procedural + substantive bench (Phase 4)."""
from __future__ import annotations

from typing import AsyncIterator

from ..state import BenchTemperament, Intent, MootCourtState
from .base import Agent, AgentResult, Message
from .context import build_bounded_context
from .schema import JudgeOutput

_PROCEDURAL = """You are the Hon'ble Judge presiding over an Indian moot court. \
This turn concerns procedure or turn-taking: whether counsel may proceed, time \
limits, or scheduling. Be brief and courteous. Address the speaker as Counsel."""

_SUBSTANTIVE = """You are the Hon'ble Judge presiding over an Indian moot court \
(Supreme Court / High Court setting). You are courteous but rigorous. You test \
the advocate's reasoning: probe weak premises, demand authority, pose hypotheticals. \
Address the speaker as Counsel. Never give legal advice or argue their case."""

_TEMPERAMENT = {
    BenchTemperament.COLD: (
        "Temperament: COLD bench — intervene sparingly; brief acknowledgement often suffices."),
    BenchTemperament.BALANCED: (
        "Temperament: BALANCED — one focused question or observation per turn."),
    BenchTemperament.HOT: (
        "Temperament: HOT — press hard; challenge assumptions immediately."),
}


class JudgeAgent(Agent):
    name = "judge"

    def _intervention_type(self, state: MootCourtState, intent: Intent | None = None) -> str:
        if intent in {Intent.PROCEDURAL, Intent.SMALL_TALK}:
            return "procedural"
        last = state.transcript[-1].intent if state.transcript else None
        if last in {Intent.PROCEDURAL, Intent.SMALL_TALK}:
            return "procedural"
        return "substantive"

    def _system(self, state: MootCourtState, intervention: str) -> str:
        base = _PROCEDURAL if intervention == "procedural" else _SUBSTANTIVE
        parts = [base]
        if state.judge_persona:
            parts.append("Additional persona: " + state.judge_persona)
        parts.append(_TEMPERAMENT.get(state.bench_temperament,
                                      _TEMPERAMENT[BenchTemperament.BALANCED]))
        parts.append(build_bounded_context(state))
        parts.append(self._voice_discipline())
        if intervention == "substantive":
            parts.append("Ask one focused question. If counsel dodged your prior question, press them.")
        else:
            parts.append("One brief procedural response only.")
        return "\n\n".join(parts)

    def _user(self, state: MootCourtState, weaknesses: list[str] | None = None) -> str:
        parts = ["Give the bench's next spoken intervention now."]
        if weaknesses and state.bench_temperament != BenchTemperament.COLD:
            parts.insert(0,
                "Private weaknesses (frame a question, do not read aloud): "
                + "; ".join(weaknesses))
        return "\n\n".join(parts)

    async def respond(self, state: MootCourtState,
                      weaknesses: list[str] | None = None,
                      intent: Intent | None = None) -> AgentResult:
        intervention = self._intervention_type(state, intent)
        text = await self.llm.chat(
            [Message("system", self._system(state, intervention)),
             Message("user", self._user(state, weaknesses))],
            model=self.model, temperature=0.6, max_tokens=180,
        )
        structured = JudgeOutput(intervention_type=intervention, question=text).to_structured()
        return AgentResult(
            agent=self.name,
            spoken_text=text,
            structured=structured,
            state_updates={"open_judge_question": text},
        )

    async def stream(self, state: MootCourtState,
                     weaknesses: list[str] | None = None,
                     intent: Intent | None = None) -> AsyncIterator[str]:
        intervention = self._intervention_type(state, intent)
        async for chunk in self.llm.stream_chat(
            [Message("system", self._system(state, intervention)),
             Message("user", self._user(state, weaknesses))],
            model=self.model, temperature=0.6, max_tokens=180,
        ):
            yield chunk
