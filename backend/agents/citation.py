"""Citation agent — normalises and verifies authorities the advocate cites.

Pipeline:
  1. Repair STT artefacts in citations (spoken numbers, spaced acronyms).
  2. Detect canonical citations (AIR / SCC / SCR ...).
  3. Verify against retrieval: does a real case match this citation? Does the
     case name the advocate used line up with the citation?
  4. If something looks misstated, surface a brief, polite flag.
"""
from __future__ import annotations

from ..retrieval.base import CompositeRetriever
from ..retrieval.citations import Citation, extract_citations
from ..state import MootCourtState
from .base import Agent, AgentResult, Message

_SYS = """You verify legal citations in an Indian moot court. You are given the \
advocate's exact words and the citations parsed from them, plus candidate real \
cases from a legal database. In at most two spoken sentences, confirm the \
citation if it matches, or politely flag a mismatch (wrong reporter, wrong \
year, or a case name that does not match the citation). If everything is fine \
and unremarkable, reply with exactly: OK."""


class CitationAgent(Agent):
    name = "citation"

    def __init__(self, llm, settings, retriever: CompositeRetriever):
        super().__init__(llm, settings)
        self.retriever = retriever

    async def verify(self, state: MootCourtState) -> AgentResult:
        text = state.last_advocate_text()
        cites = extract_citations(text)
        if not cites:
            return AgentResult(agent=self.name, data={"citations": []})

        # record normalised citations on shared state
        normalized = [c.canonical for c in cites]

        # look up the most confident citation for verification
        best: Citation = max(cites, key=lambda c: c.confidence)
        candidates = await self.retriever.search(best.canonical, limit=4)
        cand_str = "\n".join(f"- {r.short()}" for r in candidates) or "(none found)"

        spoken = await self.llm.chat(
            [Message("system", _SYS + "\n\n" + self._voice_discipline()),
             Message("user", f"Advocate said: {text}\n\n"
                             f"Parsed citations: {', '.join(normalized)}\n\n"
                             f"Database candidates:\n{cand_str}")],
            model=self.model, temperature=0.1, max_tokens=120,
        )
        spoken = spoken.strip()
        speak = "" if spoken.upper().startswith("OK") else spoken
        return AgentResult(
            agent=self.name,
            spoken_text=speak,
            detail="Citations: " + ", ".join(normalized),
            data={"citations": normalized, "candidates": [r.to_dict() for r in candidates]},
            state_updates={"cited_cases": state.cited_cases + normalized},
        )
