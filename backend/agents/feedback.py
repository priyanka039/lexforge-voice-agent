"""Feedback agent — the post-hearing bench feedback that makes moots worth it.

After (or during) a round, this agent reviews the full transcript and grades the
advocate the way a real moot bench does: a score per dimension, concrete
strengths, concrete improvements, and a short spoken summary. Returns structured
JSON for the UI plus a spoken one-liner.
"""
from __future__ import annotations

import json

from ..state import MootCourtState
from .base import Agent, AgentResult, Message

_DIMENSIONS = [
    "Articulation & structure",
    "Use of authority",
    "Responsiveness to the bench",
    "Legal soundness",
    "Court craft & decorum",
]

_SYS = """You are a senior judge giving feedback after an Indian moot court \
round. Review the transcript and grade the advocate fairly but rigorously. \
Return STRICT JSON only, with this schema:
{
  "overall_score": number (0-10, one decimal ok),
  "scores": [{"dimension": string, "score": number (0-10), "comment": string (<=20 words)}],
  "strengths": [string, string],        // 2-3 concrete points
  "improvements": [string, string],     // 2-3 concrete, actionable points
  "summary": string                     // 2 spoken sentences of overall feedback
}
Score these dimensions exactly: """ + "; ".join(_DIMENSIONS) + """.
Base every comment on what actually happened in the transcript. Be specific \
(name the moments/authorities). No prose outside the JSON."""


class FeedbackAgent(Agent):
    name = "feedback"

    async def evaluate(self, state: MootCourtState) -> AgentResult:
        transcript = state.recent_dialogue(n=60) or "(no argument was made)"
        user = (
            "Case context:\n" + state.context_block()
            + "\n\nAuthorities the advocate cited: "
            + (", ".join(state.cited_cases) or "none")
            + "\n\nFull transcript:\n" + transcript
        )
        raw = await self.llm.chat(
            [Message("system", _SYS), Message("user", user)],
            model=self.model, temperature=0.3, max_tokens=600,
        )
        fb = _parse(raw)
        spoken = fb.get("summary") or "That concludes the bench's feedback."
        return AgentResult(
            agent=self.name,
            spoken_text=spoken,
            data={"feedback": fb},
            state_updates={"feedback": fb},
        )


def _parse(raw: str) -> dict:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except Exception:
        return {"overall_score": None, "scores": [], "strengths": [],
                "improvements": [], "summary": raw[:300] or "Feedback unavailable."}
    # normalise
    data.setdefault("scores", [])
    data.setdefault("strengths", [])
    data.setdefault("improvements", [])
    data.setdefault("summary", "")
    return data
