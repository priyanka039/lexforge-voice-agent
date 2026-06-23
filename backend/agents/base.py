"""LLM client abstraction (the swappable 'any API' brain) + agent base class.

The reasoning brain is intentionally decoupled from the rest of the system via
`LLMClient`. The default implementation targets any OpenAI-compatible endpoint
(OpenAI, Azure, Together, Groq, local vLLM, ...), configured purely by
`OPENAI_API_KEY` / `OPENAI_BASE_URL`. To swap providers, implement `chat()`
and `stream_chat()` on a new subclass and pass it to the orchestrator.

If no key is configured, a deterministic offline stub keeps the whole pipeline
runnable for demos.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from ..config import Settings


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class AgentResult:
    """What a specialist returns to the orchestrator."""
    agent: str
    spoken_text: str = ""               # text to voice out (may be empty for background agents)
    detail: str = ""                    # longer text for the UI panel / transcript notes
    data: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)


class LLMClient(abc.ABC):
    """Minimal async chat interface every reasoning provider must satisfy."""

    @abc.abstractmethod
    async def chat(self, messages: list[Message], *, model: Optional[str] = None,
                   temperature: float = 0.4, max_tokens: int = 320) -> str:
        ...

    async def stream_chat(self, messages: list[Message], *, model: Optional[str] = None,
                          temperature: float = 0.4,
                          max_tokens: int = 320) -> AsyncIterator[str]:
        # Default: degrade to a single chunk. Providers override for true streaming.
        text = await self.chat(messages, model=model, temperature=temperature,
                               max_tokens=max_tokens)
        yield text

    async def close(self) -> None:
        return None


class OpenAICompatibleClient(LLMClient):
    """Targets the OpenAI Chat Completions API (and compatible endpoints)."""

    def __init__(self, settings: Settings):
        from openai import AsyncOpenAI  # imported lazily

        self.settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self.default_model = settings.reasoning_model

    async def chat(self, messages: list[Message], *, model: Optional[str] = None,
                   temperature: float = 0.4, max_tokens: int = 320) -> str:
        resp = await self._client.chat.completions.create(
            model=model or self.default_model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    async def stream_chat(self, messages: list[Message], *, model: Optional[str] = None,
                          temperature: float = 0.4,
                          max_tokens: int = 320) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model or self.default_model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    async def close(self) -> None:
        await self._client.close()


class StubClient(LLMClient):
    """Offline fallback so the system runs without any API key.

    Returns short, plausible-but-clearly-canned responses. Never used when a
    real key is configured.
    """

    async def chat(self, messages: list[Message], *, model: Optional[str] = None,
                   temperature: float = 0.4, max_tokens: int = 320) -> str:
        sys = next((m.content for m in messages if m.role == "system"), "")
        if "router" in sys.lower() or "classify" in sys.lower():
            return '{"intent": "argument", "needs_precedent": false, "topics": []}'
        if "judge" in sys.lower():
            return ("Counsel, your submission is noted. On what constitutional "
                    "provision do you principally rely?")
        return ("[offline stub] Configure OPENAI_API_KEY for real reasoning. "
                "The point you raise would normally be analysed here in two to three sentences.")


def build_llm(settings: Settings) -> LLMClient:
    if settings.has_openai:
        return OpenAICompatibleClient(settings)
    return StubClient()


class Agent(abc.ABC):
    """Base specialist. Each agent owns a narrow job and a tight system prompt."""

    name: str = "agent"
    # Each agent gets its own model knob so you can route cheap vs strong.
    model_attr: str = "reasoning_model"

    def __init__(self, llm: LLMClient, settings: Settings):
        self.llm = llm
        self.settings = settings

    @property
    def model(self) -> str:
        return getattr(self.settings, self.model_attr, self.settings.reasoning_model)

    def _voice_discipline(self) -> str:
        n = self.settings.max_spoken_sentences
        return (
            f"You are speaking out loud in a live moot court. Respond in at most "
            f"{n} spoken sentences. Plain spoken English. No markdown, no lists, "
            f"no 'firstly/secondly', no headings, no emojis. State the substance "
            f"and the case name; nothing else."
        )
