"""Precedent agent — finds and states the most on-point authority.

Retrieves live (Indian Kanoon API / web) + seed corpus, then asks the LLM to
pick the single most relevant case and state its holding in a spoken sentence
or two. Also pre-fetches in the background so it is ready before being asked.
"""
from __future__ import annotations

from ..retrieval.base import CompositeRetriever, PrecedentResult
from ..state import MootCourtState
from .base import Agent, AgentResult, Message

_SYS = """You are a legal research specialist for Indian moot court. You are \
given a set of candidate authorities retrieved from Indian Kanoon / law \
reports. Pick the single most on-point case for the advocate's current point \
and state, out loud, its name, citation, and the proposition it supports. If \
none of the candidates truly fit, say so briefly and name the closest. Never \
invent a citation that is not in the candidates."""


class PrecedentAgent(Agent):
    name = "precedent"

    def __init__(self, llm, settings, retriever: CompositeRetriever):
        super().__init__(llm, settings)
        self.retriever = retriever

    async def find(self, state: MootCourtState, query: str | None = None,
                   limit: int = 5) -> list[PrecedentResult]:
        q = query or state.last_advocate_text() or " ".join(state.discussed_topics[-3:])
        if not q.strip():
            return []
        return await self.retriever.search(q, limit=limit)

    async def respond(self, state: MootCourtState,
                      query: str | None = None) -> AgentResult:
        results = await self.find(state, query=query, limit=5)
        if not results:
            return AgentResult(
                agent=self.name,
                spoken_text="I could not locate a directly applicable authority on that point.",
                data={"precedents": []},
            )
        candidates = "\n".join(f"- {r.short()} [{r.source}]" for r in results)
        spoken = await self.llm.chat(
            [Message("system", self._sys()),
             Message("user", f"Advocate's point: {state.last_advocate_text()}\n\n"
                             f"Candidate authorities:\n{candidates}\n\n"
                             "State the single best authority out loud now.")],
            model=self.model, temperature=0.2, max_tokens=160,
        )
        return AgentResult(
            agent=self.name,
            spoken_text=spoken,
            detail=candidates,
            data={"precedents": [r.to_dict() for r in results]},
            state_updates={"prefetched_precedents": [r.to_dict() for r in results]},
        )

    async def prefetch(self, state: MootCourtState) -> AgentResult:
        """Background pre-fetch: warms precedents for the current topics without speaking."""
        results = await self.find(state, limit=5)
        return AgentResult(
            agent=self.name,
            data={"precedents": [r.to_dict() for r in results]},
            state_updates={"prefetched_precedents": [r.to_dict() for r in results]},
        )

    def _sys(self) -> str:
        return _SYS + "\n\n" + self._voice_discipline()
