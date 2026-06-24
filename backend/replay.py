"""Deterministic E2E replay harness for verification (Phase 9)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents.base import LLMClient, Message, StubClient
from .canonical import canonical_copy, digest
from .config import Settings
from .ledger import authority_set_digest, ledger_digest_from_state
from .runtime import EventSource
from .session_runtime import SessionRuntime
from .state import MootCourtState

_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "e2e"

# Audit-only fields excluded from determinism comparison.
_STRIP_KEYS = frozenset({"ts", "started_at", "created_at", "envelope_id"})


class FixtureLLM(StubClient):
    """Returns canned LLM outputs keyed by prompt digest for stable replay."""

    def __init__(self, fixtures: dict[str, str] | None = None):
        self._fixtures = fixtures or {}
        self.call_log: list[str] = []

    def _prompt_key(self, messages: list[Message]) -> str:
        payload = [{"role": m.role, "content": m.content} for m in messages]
        return digest(payload)

    async def chat(self, messages: list[Message], *, model=None,
                   temperature: float = 0.4, max_tokens: int = 320) -> str:
        key = self._prompt_key(messages)
        self.call_log.append(key)
        if key in self._fixtures:
            return self._fixtures[key]
        return await super().chat(messages, model=model, temperature=temperature,
                                  max_tokens=max_tokens)


@dataclass(frozen=True)
class ReplaySnapshot:
    session_id: str
    transition_digest: str
    ledger_digest: str
    authority_set_digest: str
    vocab_digest: str
    session_canonical: dict[str, Any]
    session_canonical_digest: str


def strip_nondeterministic(value: Any) -> Any:
    """Remove audit timestamps and envelope noise for stable comparison."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _STRIP_KEYS:
                continue
            out[k] = strip_nondeterministic(v)
        return out
    if isinstance(value, list):
        return [strip_nondeterministic(v) for v in value]
    return value


def load_event_log(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or (_FIXTURES / "event_log.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("turns", []))


def load_llm_fixtures(path: Path | None = None) -> dict[str, str]:
    path = path or (_FIXTURES / "llm_responses.json")
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return dict(json.load(f))


def capture_snapshot(runtime: SessionRuntime) -> ReplaySnapshot:
    state = runtime.state
    state.to_dict()  # sync ledger
    session = strip_nondeterministic(canonical_copy(state.to_dict()))
    ledger = state.ledger
    return ReplaySnapshot(
        session_id=state.session_id,
        transition_digest=runtime.engine.to_dict()["transition_digest"],
        ledger_digest=ledger_digest_from_state(state),
        authority_set_digest=authority_set_digest(ledger) if ledger else digest({"empty": True}),
        vocab_digest=runtime.vocab.terms_digest,
        session_canonical=session,
        session_canonical_digest=digest(session),
    )


async def run_replay(
    settings: Settings,
    turns: list[dict[str, Any]],
    *,
    session_id: str = "e2e-replay-01",
    llm_fixtures: dict[str, str] | None = None,
) -> ReplaySnapshot:
    """Process a fixed event log through SessionRuntime with mocked LLM."""
    state = MootCourtState(session_id=session_id)
    runtime = SessionRuntime(settings, state=state, store=None)
    runtime.orchestrator.llm = FixtureLLM(llm_fixtures or load_llm_fixtures())

    async def _noop(_: dict) -> None:
        return None

    await runtime.start(_noop)
    try:
        for turn in turns:
            text = str(turn.get("text", "")).strip()
            if not text:
                continue
            source_name = str(turn.get("source", "ui")).lower()
            source = EventSource(source_name) if source_name in EventSource._value2member_map_ else EventSource.UI
            turn_id = turn.get("turn_id")
            ok = await runtime.enqueue_user_text(text, source=source, turn_id=turn_id)
            if not ok:
                raise RuntimeError(f"enqueue failed for turn: {text!r}")
            await asyncio.sleep(0.05)
        # Drain consumer
        await asyncio.sleep(0.3)
    finally:
        await runtime.close()

    return capture_snapshot(runtime)


def run_replay_sync(*args, **kwargs) -> ReplaySnapshot:
    return asyncio.run(run_replay(*args, **kwargs))
