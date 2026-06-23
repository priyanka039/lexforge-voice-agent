"""Counter-argument agent — opposing counsel's rebuttal.

Voices the strongest argument the other side would make against the advocate's
latest submission. Used when the user asks to spar with opposing counsel, or to
feed the Judge sharper hypotheticals.
"""
from __future__ import annotations

from ..state import MootCourtState
from .base import Agent, AgentResult, Message

_SYS = """You are opposing counsel in an Indian moot court. Your job is to \
deliver the single most damaging rebuttal to the advocate's latest submission: \
attack the weakest link, distinguish their authorities, or invoke a competing \
principle. Be incisive and respectful. Ground rebuttals in Indian law where \
possible and name a case if one clearly applies."""


class CounterAgent(Agent):
    name = "counter"

    async def respond(self, state: MootCourtState) -> AgentResult:
        spoken = await self.llm.chat(
            [Message("system", _SYS + "\n\n" + self._voice_discipline()),
             Message("user", "Case context:\n" + state.context_block()
                     + "\n\nRecent exchange:\n" + state.recent_dialogue(6)
                     + "\n\nDeliver opposing counsel's strongest rebuttal now.")],
            model=self.model, temperature=0.6, max_tokens=180,
        )
        return AgentResult(agent=self.name, spoken_text=spoken)
