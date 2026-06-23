"""Judge agent — the AI bench.

The most distinctive feature: a presiding judge of an Indian court who probes
the advocate's submissions with sharp, doctrinally-grounded questions. This is
the agent that usually *speaks*, and it streams sentence-by-sentence so TTS can
start before the full answer is ready.
"""
from __future__ import annotations

from typing import AsyncIterator

from ..state import BenchTemperament, MootCourtState
from .base import Agent, AgentResult, Message

_PERSONA = """You are the Hon'ble Judge presiding over an Indian moot court \
(Supreme Court / High Court setting). You are courteous but rigorous, in the \
tradition of an Indian appellate bench. You test the advocate's reasoning: you \
probe weak premises, demand authority for propositions, pose hypotheticals, and \
occasionally play devil's advocate. You address the speaker as "Counsel" or \
"Mr./Ms. Counsel". You may say "Yes, counsel", "Let me stop you there", or \
"Assuming that is so, how do you meet...". You never give legal advice to the \
advocate and never argue their case for them; you interrogate it."""

_TEMPERAMENT = {
    BenchTemperament.COLD: (
        "Temperament: a COLD bench. Let counsel develop their argument. Intervene "
        "sparingly and only on a genuinely important point; often a brief "
        "acknowledgement is enough."),
    BenchTemperament.BALANCED: (
        "Temperament: a BALANCED appellate bench. Engage with one focused question "
        "or observation per turn."),
    BenchTemperament.HOT: (
        "Temperament: a HOT, interventionist bench. Press hard with sharp, rapid "
        "questions; challenge assumptions immediately and do not let weak premises "
        "pass."),
}


class JudgeAgent(Agent):
    name = "judge"

    def _system(self, state: MootCourtState) -> str:
        parts = [_PERSONA]
        if state.judge_persona:
            parts.append("Additional persona direction: " + state.judge_persona)
        parts.append(_TEMPERAMENT.get(state.bench_temperament,
                                      _TEMPERAMENT[BenchTemperament.BALANCED]))
        parts.append("Case context:\n" + state.context_block())
        parts.append(self._voice_discipline())
        parts.append("Ask one focused question or make one pointed observation per "
                     "turn. If the advocate dodged your previous question, press "
                     "them on it.")
        return "\n\n".join(parts)

    def _user(self, state: MootCourtState, weaknesses: list[str] | None = None) -> str:
        parts = ["Recent exchange:\n" + state.recent_dialogue(8)]
        if weaknesses:
            parts.append(
                "Privately, these weaknesses exist in the advocate's position "
                "(use them to frame a probing question, do not read them out): "
                + "; ".join(weaknesses)
            )
        parts.append("Give the bench's next spoken intervention now.")
        return "\n\n".join(parts)

    async def respond(self, state: MootCourtState,
                      weaknesses: list[str] | None = None) -> AgentResult:
        text = await self.llm.chat(
            [Message("system", self._system(state)),
             Message("user", self._user(state, weaknesses))],
            model=self.model, temperature=0.6, max_tokens=180,
        )
        return AgentResult(agent=self.name, spoken_text=text,
                           state_updates={"open_judge_question": text})

    async def stream(self, state: MootCourtState,
                     weaknesses: list[str] | None = None) -> AsyncIterator[str]:
        async for chunk in self.llm.stream_chat(
            [Message("system", self._system(state)),
             Message("user", self._user(state, weaknesses))],
            model=self.model, temperature=0.6, max_tokens=180,
        ):
            yield chunk
