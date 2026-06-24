"""Specialist agents (the OpenAI-backed brain)."""
from __future__ import annotations

from .base import Agent, AgentResult, LLMClient, Message, build_llm
from .advisor import AdvisorAgent, WeaknessAgent
from .citation import CitationAgent
from .counter import CounterAgent
from .feedback import FeedbackAgent
from .judge import JudgeAgent
from .precedent import PrecedentAgent

__all__ = [
    "Agent",
    "AgentResult",
    "LLMClient",
    "Message",
    "build_llm",
    "JudgeAgent",
    "PrecedentAgent",
    "CounterAgent",
    "WeaknessAgent",
    "AdvisorAgent",
    "CitationAgent",
    "FeedbackAgent",
]
