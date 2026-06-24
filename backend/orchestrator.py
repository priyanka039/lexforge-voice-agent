"""Supervisor / orchestrator.

A thin, fast layer that does four things and never reasons about law itself:
  1. Classify the advocate's intent (fast router model).
  2. Maintain shared MootCourtState (the blackboard).
  3. Dispatch specialists — background ones in parallel, the speaking one streamed.
  4. Manage turn-taking and emit a stream of events for voice + UI.

It yields `Event`s: spoken sentences (for sentence-level TTS pipelining),
agent-activity updates (for the UI panel), and structured data (precedents).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from .agents import (CitationAgent, CounterAgent, FeedbackAgent, JudgeAgent,
                     LLMClient, Message, PrecedentAgent, WeaknessAgent, build_llm)
from .config import Settings
from .ledger import (
    FinalLedger,
    apply_agent_output_to_ledger,
    compress_ledger,
    recompute_confidence,
    sync_ledger_from_state,
)
from .language import validate_and_rewrite_spoken
from .retrieval import build_retriever, detect_topics, extract_citations
from .runtime import VocabSnapshot
from .state import Intent, MootCourtState, PracticeMode, Speaker


@dataclass
class Event:
    type: str                       # see EventType constants below
    data: dict[str, Any] = field(default_factory=dict)


class EventType:
    INTENT = "intent"               # router classified the turn
    AGENT_START = "agent_start"     # a specialist began work
    AGENT_DONE = "agent_done"       # a specialist finished
    SPEAK_SENTENCE = "speak_sentence"  # a complete sentence ready for TTS
    SPEAK_DONE = "speak_done"       # speaker finished its full reply
    PRECEDENTS = "precedents"       # structured case list for the UI
    NOTE = "note"                   # misc note for the UI/transcript
    FEEDBACK = "feedback"           # structured performance feedback for the UI
    TURN_DONE = "turn_done"         # end of processing this advocate turn


_ROUTER_SYS = """You are a fast intent router for an Indian moot court voice \
assistant. Given the advocate's latest utterance and recent context, output \
STRICT JSON (no prose) with this schema:
{
  "intent": one of ["opening","argument","answer_to_judge","request_precedent",
                     "request_counter","cite_case","request_help","request_feedback",
                     "procedural","small_talk","unknown"],
  "needs_precedent": boolean,   // would a case citation strengthen this turn?
  "addresses": one of ["judge","opposing","system"],
  "topics": [up to 4 short legal topic strings]
}
Guidance: "argument"/"opening" = substantive submission; "answer_to_judge" = \
responding to the bench's pending question; "request_precedent" = explicitly \
asking for case law; "request_counter" = asking opposing counsel to rebut; \
"cite_case" = advocate is themselves citing an authority; "request_help" = \
asking the assistant/coach for guidance; "request_feedback" = asking how they \
did / for an assessment of their performance; "procedural" = "may I proceed, my \
lord" etc."""


class Router:
    def __init__(self, llm: LLMClient, settings: Settings):
        self.llm = llm
        self.settings = settings

    async def classify(self, state: MootCourtState, text: str) -> dict[str, Any]:
        # Cheap deterministic pre-checks first (latency + robustness).
        cites = extract_citations(text)
        try:
            raw = await self.llm.chat(
                [Message("system", _ROUTER_SYS),
                 Message("user", f"Recent context:\n{state.recent_dialogue(5)}\n\n"
                                 f"Advocate's latest utterance:\n{text}")],
                model=self.settings.router_model, temperature=0.0, max_tokens=160,
            )
            parsed = _safe_json(raw)
        except Exception:
            parsed = {}

        intent = parsed.get("intent", "unknown")
        # Hard overrides from deterministic signals.
        if cites and intent not in {"request_precedent", "request_help"}:
            intent = "cite_case"
        result = {
            "intent": intent,
            "needs_precedent": bool(parsed.get("needs_precedent", False)) or bool(cites),
            "addresses": parsed.get("addresses", "judge"),
            "topics": parsed.get("topics", []) or detect_topics(text),
            "citations": [c.canonical for c in cites],
        }
        return result


class Orchestrator:
    def __init__(self, settings: Settings, *, vocab: VocabSnapshot | None = None):
        self.settings = settings
        self.vocab = vocab
        self.llm: LLMClient = build_llm(settings)
        self.retriever = build_retriever(settings, vocab=vocab)
        self.router = Router(self.llm, settings)
        self.judge = JudgeAgent(self.llm, settings)
        self.precedent = PrecedentAgent(self.llm, settings, self.retriever)
        self.counter = CounterAgent(self.llm, settings)
        self.weakness = WeaknessAgent(self.llm, settings)
        self.citation = CitationAgent(self.llm, settings, self.retriever)
        self.feedback = FeedbackAgent(self.llm, settings)
        self.state = MootCourtState()

    async def close(self) -> None:
        await asyncio.gather(self.llm.close(), self.retriever.close(),
                             return_exceptions=True)

    # ---- main entry point: process one advocate utterance ----
    async def handle_turn(self, text: str) -> AsyncIterator[Event]:
        text = text.strip()
        if not text:
            return

        self.state.add_turn(Speaker.ADVOCATE, text)
        route = await self.router.classify(self.state, text)
        intent = _to_intent(route["intent"])
        self.state.transcript[-1].intent = intent
        for t in route["topics"]:
            if t not in self.state.discussed_topics:
                self.state.discussed_topics.append(t)
        yield Event(EventType.INTENT, route)

        # Kick off background specialists in parallel (they don't block speech).
        bg_tasks: list[asyncio.Task] = [
            asyncio.create_task(self._run_bg("weakness", self.weakness.analyze(self.state))),
        ]
        if route["needs_precedent"]:
            bg_tasks.append(asyncio.create_task(
                self._run_bg("precedent", self.precedent.prefetch(self.state))))

        # Decide who speaks, then stream their reply sentence-by-sentence.
        speaker_emitted = False
        async for ev in self._dispatch_speaker(intent, route):
            if ev.type == EventType.SPEAK_SENTENCE:
                speaker_emitted = True
            yield ev

        # Drain background tasks, surfacing their results to the UI/state.
        for done in await asyncio.gather(*bg_tasks, return_exceptions=True):
            if isinstance(done, Exception) or done is None:
                continue
            for ev in done:
                if ev.type == EventType.AGENT_DONE and ev.data.get("data"):
                    apply_agent_output_to_ledger(
                        self.state, ev.data.get("agent", ""), ev.data.get("data", {}))
                elif ev.type == EventType.PRECEDENTS:
                    apply_agent_output_to_ledger(
                        self.state, "precedent", {"precedents": ev.data.get("precedents", [])})
                elif ev.type == EventType.NOTE and ev.data.get("weaknesses"):
                    apply_agent_output_to_ledger(
                        self.state, "weakness", {"weaknesses": ev.data.get("weaknesses", [])})
                yield ev

        if not speaker_emitted:
            # Safety net: the bench always says *something* (court mode only).
            if self.state.practice_mode != PracticeMode.DEBATE:
                async for ev in self._stream_agent(
                        "judge",
                        self.judge.stream(self.state, intent=intent),
                        role=Speaker.JUDGE):
                    yield ev

        self._finalize_ledger()
        yield Event(EventType.TURN_DONE, {"state": self.state.to_dict()})

    # ---- explicit feedback (also used by the 'End hearing' button) ----
    async def run_feedback(self) -> AsyncIterator[Event]:
        yield Event(EventType.AGENT_START, {"agent": "feedback"})
        result = await self.feedback.evaluate(self.state)
        self._apply_state(result.state_updates)
        if result.data.get("feedback"):
            yield Event(EventType.FEEDBACK, {"feedback": result.data["feedback"]})
        text = (result.spoken_text or "").strip()
        if text:
            self.state.add_turn(Speaker.JUDGE, text, meta={"agent": "feedback"})
            for sentence in _split_sentences(text):
                yield Event(EventType.SPEAK_SENTENCE, {"agent": "feedback", "text": sentence})
        yield Event(EventType.AGENT_DONE, {"agent": "feedback"})
        yield Event(EventType.SPEAK_DONE, {"agent": "feedback", "text": text})

    # ---- routing to the speaking agent ----
    async def _dispatch_speaker(self, intent: Intent,
                                route: dict[str, Any]) -> AsyncIterator[Event]:
        if intent == Intent.REQUEST_PRECEDENT:
            async for ev in self._speak_result("precedent",
                                               self.precedent.respond(self.state)):
                yield ev
            # bench follows up after the authority is stated
            async for ev in self._stream_agent(
                    "judge",
                    self.judge.stream(self.state, intent=intent),
                    role=Speaker.JUDGE):
                yield ev
            return

        if intent == Intent.REQUEST_COUNTER:
            async for ev in self._speak_result("counter", self.counter.respond(self.state)):
                yield ev
            return

        if intent == Intent.REQUEST_HELP:
            async for ev in self._speak_result("weakness", self.weakness.coach(self.state)):
                yield ev
            return

        if intent == Intent.REQUEST_FEEDBACK:
            async for ev in self.run_feedback():
                yield ev
            return

        if intent == Intent.CITE_CASE:
            # verify the citation first; if flagged, the bench speaks the flag's gist
            result = await self._await_result("citation", self.citation.verify(self.state))
            for ev in result["events"]:
                yield ev
            # then the judge engages with the substance
            async for ev in self._stream_agent(
                    "judge",
                    self.judge.stream(self.state, weaknesses=self.state.known_weaknesses,
                                      intent=intent),
                    role=Speaker.JUDGE):
                yield ev
            return

        # Default: opening / argument / answer_to_judge / procedural / small_talk
        if self.state.practice_mode == PracticeMode.DEBATE:
            async for ev in self._speak_result("counter", self.counter.respond(self.state)):
                yield ev
            return

        async for ev in self._stream_agent(
                "judge",
                self.judge.stream(self.state, weaknesses=self.state.known_weaknesses,
                                  intent=intent),
                role=Speaker.JUDGE):
            yield ev

    # ---- helpers ----
    async def _stream_agent(self, agent_name: str, gen, role: Speaker) -> AsyncIterator[Event]:
        """Stream an agent's tokens, emitting complete sentences for TTS."""
        yield Event(EventType.AGENT_START, {"agent": agent_name})
        acc = _SentenceAccumulator()
        full: list[str] = []
        try:
            async for chunk in gen:
                full.append(chunk)
                for sentence in acc.push(chunk):
                    sentence = validate_and_rewrite_spoken(sentence, self.state.language)
                    yield Event(EventType.SPEAK_SENTENCE,
                                {"agent": agent_name, "text": sentence})
        except Exception as e:  # never let a model error kill the turn
            yield Event(EventType.NOTE, {"agent": agent_name, "error": str(e)})
        tail = acc.flush()
        if tail:
            tail = validate_and_rewrite_spoken(tail, self.state.language)
            yield Event(EventType.SPEAK_SENTENCE, {"agent": agent_name, "text": tail})
        text = "".join(full).strip()
        text = validate_and_rewrite_spoken(text, self.state.language)
        if text:
            self.state.add_turn(role, text)
            if agent_name == "judge":
                self.state.open_judge_question = text
        yield Event(EventType.AGENT_DONE, {"agent": agent_name})
        yield Event(EventType.SPEAK_DONE, {"agent": agent_name, "text": text})

    async def _speak_result(self, agent_name: str, coro) -> AsyncIterator[Event]:
        """Run a non-streaming agent, then chunk its reply into sentences."""
        yield Event(EventType.AGENT_START, {"agent": agent_name})
        result = await coro
        self._apply_state(result.state_updates)
        if result.structured:
            self._record_structured(agent_name, result.spoken_text, result.structured)
        if result.data.get("precedents"):
            yield Event(EventType.PRECEDENTS, {"precedents": result.data["precedents"]})
        text = (result.spoken_text or "").strip()
        text = validate_and_rewrite_spoken(text, self.state.language)
        if text:
            role = Speaker.JUDGE if agent_name == "judge" else Speaker.SYSTEM
            self.state.add_turn(role, text, meta={"agent": agent_name})
            for sentence in _split_sentences(text):
                yield Event(EventType.SPEAK_SENTENCE, {"agent": agent_name, "text": sentence})
        yield Event(EventType.AGENT_DONE, {"agent": agent_name})
        yield Event(EventType.SPEAK_DONE, {"agent": agent_name, "text": text})

    async def _await_result(self, agent_name: str, coro) -> dict[str, Any]:
        """Like _speak_result but collected into a list (used inline)."""
        events: list[Event] = [Event(EventType.AGENT_START, {"agent": agent_name})]
        result = await coro
        self._apply_state(result.state_updates)
        if result.data.get("citations"):
            events.append(Event(EventType.NOTE,
                                {"agent": agent_name, "citations": result.data["citations"]}))
        text = (result.spoken_text or "").strip()
        if text:
            self.state.add_turn(Speaker.SYSTEM, text, meta={"agent": agent_name})
            for sentence in _split_sentences(text):
                events.append(Event(EventType.SPEAK_SENTENCE,
                                    {"agent": agent_name, "text": sentence}))
        events.append(Event(EventType.AGENT_DONE, {"agent": agent_name}))
        return {"events": events, "result": result}

    async def _run_bg(self, agent_name: str, coro) -> list[Event]:
        try:
            result = await coro
        except Exception as e:
            return [Event(EventType.NOTE, {"agent": agent_name, "error": str(e)})]
        self._apply_state(result.state_updates)
        out = [Event(EventType.AGENT_DONE, {"agent": agent_name, "background": True,
                                            "data": result.data})]
        if result.data.get("precedents"):
            out.append(Event(EventType.PRECEDENTS, {"precedents": result.data["precedents"]}))
        if result.data.get("weaknesses"):
            out.append(Event(EventType.NOTE, {"agent": agent_name,
                                              "weaknesses": result.data["weaknesses"]}))
        return out

    def _apply_state(self, updates: dict[str, Any]) -> None:
        for k, v in (updates or {}).items():
            if k == "cited_cases":
                # de-dupe accumulation
                merged = list(dict.fromkeys(self.state.cited_cases + list(v)))
                self.state.cited_cases = merged
            else:
                setattr(self.state, k, v)

    def _finalize_ledger(self) -> None:
        sync_ledger_from_state(self.state)
        if self.state.ledger:
            final = compress_ledger(self.state.ledger)
            self.state.ledger.confidence_by_issue = recompute_confidence(final).by_issue

    def _record_structured(self, agent: str, spoken: str, structured: dict[str, Any]) -> None:
        if not hasattr(self.state, "agent_outputs"):
            self.state.agent_outputs = []
        self.state.agent_outputs.append({
            "agent": agent,
            "spoken_text": spoken,
            "structured": structured,
        })


# ---------- small utilities ----------
_SENTENCE_RX = re.compile(r"(.+?[.!?])(\s+|$)", re.DOTALL)


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out = [m.group(1).strip() for m in _SENTENCE_RX.finditer(text)]
    consumed = sum(len(m.group(0)) for m in _SENTENCE_RX.finditer(text))
    if consumed < len(text):
        out.append(text[consumed:].strip())
    return [s for s in out if s]


class _SentenceAccumulator:
    """Buffers streamed tokens and releases complete sentences for TTS."""

    def __init__(self) -> None:
        self.buf = ""

    def push(self, chunk: str) -> list[str]:
        self.buf += chunk
        out: list[str] = []
        while True:
            m = re.search(r"[.!?](\s|$)", self.buf)
            if not m:
                break
            end = m.end()
            sentence = self.buf[:end].strip()
            self.buf = self.buf[end:]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> str:
        s = self.buf.strip()
        self.buf = ""
        return s


def _safe_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _to_intent(name: str) -> Intent:
    try:
        return Intent(name)
    except Exception:
        return Intent.UNKNOWN
