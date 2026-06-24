"""Schema-first structured agent outputs (Phase 4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentOutput:
    """Canonical agent return shape stored in session JSON."""

    agent: str
    spoken_text: str = ""
    structured: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "spoken_text": self.spoken_text,
            "structured": self.structured,
        }


@dataclass
class WeaknessOutput:
    weaknesses: list[str] = field(default_factory=list)

    def to_structured(self) -> dict[str, Any]:
        return {"weaknesses": list(self.weaknesses)}


@dataclass
class PrecedentOutput:
    precedents: list[dict[str, Any]] = field(default_factory=list)
    selected_title: str = ""

    def to_structured(self) -> dict[str, Any]:
        return {
            "precedents": list(self.precedents),
            "selected_title": self.selected_title,
        }


@dataclass
class JudgeOutput:
    intervention_type: str = "substantive"  # procedural | substantive
    question: str = ""

    def to_structured(self) -> dict[str, Any]:
        return {
            "intervention_type": self.intervention_type,
            "question": self.question,
        }


@dataclass
class CitationOutput:
    citations: list[str] = field(default_factory=list)
    verified: bool = False

    def to_structured(self) -> dict[str, Any]:
        return {
            "citations": list(self.citations),
            "verified": self.verified,
        }
