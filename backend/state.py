"""Shared moot-court session state.

This is the single object all specialist agents read from and write to
(the "blackboard" in the supervisor/worker pattern). Keeping it explicit and
serialisable makes it easy to persist the transcript to an external Matter
system later.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Speaker(str, Enum):
    ADVOCATE = "advocate"   # the human user arguing the case
    JUDGE = "judge"         # the AI bench
    SYSTEM = "system"


class Intent(str, Enum):
    # What the advocate's last utterance is trying to do.
    OPENING = "opening"                  # opening statement / submission
    ARGUMENT = "argument"                # substantive legal argument
    ANSWER_TO_JUDGE = "answer_to_judge"  # responding to a bench question
    REQUEST_PRECEDENT = "request_precedent"   # explicitly asking for case law
    REQUEST_COUNTER = "request_counter"  # asking opposing counsel to rebut
    CITE_CASE = "cite_case"              # advocate is citing authority
    REQUEST_HELP = "request_help"        # asking the system for coaching
    REQUEST_FEEDBACK = "request_feedback"  # asking for performance feedback
    PROCEDURAL = "procedural"            # "may I proceed", "my lord", etc.
    SMALL_TALK = "small_talk"
    UNKNOWN = "unknown"


class BenchTemperament(str, Enum):
    """How active the bench is."""
    COLD = "cold"          # lets counsel develop the argument; few interruptions
    BALANCED = "balanced"  # default appellate bench
    HOT = "hot"            # interventionist; frequent, sharp questions


@dataclass
class TranscriptTurn:
    speaker: Speaker
    text: str
    ts: float = field(default_factory=time.time)
    intent: Optional[Intent] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker.value,
            "text": self.text,
            "ts": self.ts,
            "intent": self.intent.value if self.intent else None,
            "meta": self.meta,
        }


@dataclass
class CaseBrief:
    """The matter being argued. Seeds every agent's context."""
    title: str = "Untitled Moot"
    court: str = "Supreme Court of India"
    appellant: str = "Appellant"
    respondent: str = "Respondent"
    user_side: str = "appellant"           # which side the human argues
    facts: str = ""
    issues: list[str] = field(default_factory=list)
    propositions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"Matter: {self.title} before the {self.court}."]
        parts.append(f"Parties: {self.appellant} (appellant) v. {self.respondent} (respondent).")
        parts.append(f"The advocate represents the {self.user_side}.")
        if self.facts:
            parts.append(f"Facts: {self.facts}")
        if self.issues:
            parts.append("Issues: " + "; ".join(self.issues))
        return "\n".join(parts)


@dataclass
class MootCourtState:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    brief: CaseBrief = field(default_factory=CaseBrief)
    transcript: list[TranscriptTurn] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # Bench configuration (settable from the UI).
    bench_temperament: BenchTemperament = BenchTemperament.BALANCED
    judge_persona: str = ""              # optional extra persona flavour

    # Latest performance feedback, if generated.
    feedback: Optional[dict[str, Any]] = None

    # Live working memory updated by background specialists.
    cited_cases: list[str] = field(default_factory=list)          # citations the advocate has used
    discussed_topics: list[str] = field(default_factory=list)     # rolling list of legal topics
    open_judge_question: Optional[str] = None                     # last unanswered bench question
    known_weaknesses: list[str] = field(default_factory=list)     # weakness-agent findings
    prefetched_precedents: list[dict[str, Any]] = field(default_factory=list)

    turn_index: int = 0

    # ---- transcript helpers ----
    def add_turn(self, speaker: Speaker, text: str, intent: Optional[Intent] = None,
                 **meta: Any) -> TranscriptTurn:
        turn = TranscriptTurn(speaker=speaker, text=text, intent=intent, meta=meta)
        self.transcript.append(turn)
        if speaker == Speaker.ADVOCATE:
            self.turn_index += 1
        return turn

    def recent_dialogue(self, n: int = 8) -> str:
        rows = self.transcript[-n:]
        out = []
        for t in rows:
            label = {"advocate": "ADVOCATE", "judge": "JUDGE", "system": "SYSTEM"}[t.speaker.value]
            out.append(f"{label}: {t.text}")
        return "\n".join(out)

    def last_advocate_text(self) -> str:
        for t in reversed(self.transcript):
            if t.speaker == Speaker.ADVOCATE:
                return t.text
        return ""

    def context_block(self) -> str:
        """Compact shared context handed to every specialist."""
        lines = [self.brief.summary()]
        lines.append(f"Bench temperament: {self.bench_temperament.value}.")
        if self.open_judge_question:
            lines.append(f"Pending bench question: {self.open_judge_question}")
        if self.cited_cases:
            lines.append("Authorities cited so far: " + ", ".join(self.cited_cases[-8:]))
        if self.discussed_topics:
            lines.append("Topics in play: " + ", ".join(self.discussed_topics[-8:]))
        if self.known_weaknesses:
            lines.append("Known weaknesses in advocate's case: "
                         + "; ".join(self.known_weaknesses[-4:]))
        return "\n".join(lines)

    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "brief": self.brief.__dict__,
            "bench_temperament": self.bench_temperament.value,
            "judge_persona": self.judge_persona,
            "transcript": [t.to_dict() for t in self.transcript],
            "cited_cases": self.cited_cases,
            "discussed_topics": self.discussed_topics,
            "open_judge_question": self.open_judge_question,
            "known_weaknesses": self.known_weaknesses,
            "turn_index": self.turn_index,
            "feedback": self.feedback,
        }

    def to_markdown(self) -> str:
        """A readable transcript suitable for saving to a Matter file."""
        b = self.brief
        lines = [
            f"# Moot Court Transcript — {b.title}",
            "",
            f"- **Court:** {b.court}",
            f"- **Parties:** {b.appellant} (appellant) v. {b.respondent} (respondent)",
            f"- **Counsel for:** {b.user_side}",
            f"- **Session:** `{self.session_id}`",
            f"- **Duration:** {int(self.elapsed_seconds() // 60)} min",
        ]
        if b.issues:
            lines.append("- **Issues:**")
            lines += [f"  {i + 1}. {iss}" for i, iss in enumerate(b.issues)]
        if self.cited_cases:
            lines.append("- **Authorities cited:** " + ", ".join(self.cited_cases))
        lines += ["", "## Proceedings", ""]
        labels = {"advocate": "**Counsel**", "judge": "**The Bench**", "system": "_Assistant_"}
        for t in self.transcript:
            lines.append(f"{labels.get(t.speaker.value, t.speaker.value)}: {t.text}")
            lines.append("")
        if self.feedback:
            fb = self.feedback
            lines += ["## Bench Feedback", ""]
            if fb.get("overall_score") is not None:
                lines.append(f"**Overall: {fb['overall_score']}/10**")
                lines.append("")
            for s in fb.get("scores", []):
                lines.append(f"- {s.get('dimension')}: {s.get('score')}/10 — {s.get('comment','')}")
            if fb.get("strengths"):
                lines += ["", "**Strengths**"] + [f"- {x}" for x in fb["strengths"]]
            if fb.get("improvements"):
                lines += ["", "**Areas to improve**"] + [f"- {x}" for x in fb["improvements"]]
            if fb.get("summary"):
                lines += ["", fb["summary"]]
        return "\n".join(lines)
