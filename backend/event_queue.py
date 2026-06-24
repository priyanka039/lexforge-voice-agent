"""Serial event queue with single-writer sequencer (R1, R5, R8)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .ingress import ExternalInputEnvelope, IngressSource, wrap_external_input
from .runtime import EventSource, RuntimeEvent

MAX_PENDING_EVENTS = 256


class SourcePriority(IntEnum):
    SYSTEM = 0
    ORCHESTRATOR = 1
    UI = 2
    VOICE = 3
    RETRIEVAL = 4
    AGENT = 5


_SOURCE_MAP = {
    EventSource.SYSTEM: SourcePriority.SYSTEM,
    EventSource.ORCHESTRATOR: SourcePriority.ORCHESTRATOR,
    EventSource.UI: SourcePriority.UI,
    EventSource.VOICE: SourcePriority.VOICE,
    EventSource.RETRIEVAL: SourcePriority.RETRIEVAL,
    EventSource.AGENT: SourcePriority.AGENT,
}


@dataclass
class PendingEvent:
    type: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    turn_version: int | None = None
    source: EventSource = EventSource.ORCHESTRATOR
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    envelope: ExternalInputEnvelope | None = None
    is_deferred: bool = False
    event_seq: int = 0
    ingest_seq: int = 0


@dataclass
class EnqueueResult:
    accepted: bool
    event: PendingEvent | None = None
    reason: str | None = None


class EventSequencer:
    """Single-writer atomic event_seq + ingest_seq assignment (R1)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._lock = asyncio.Lock()
        self._event_seq = 0
        self._ingest_seq = 0
        self._queue: asyncio.Queue[PendingEvent | None] = asyncio.Queue(maxsize=MAX_PENDING_EVENTS)
        self.inflight_event_count = 0
        self.active_turn_version = 0
        self._inflight_retrieval: set[str] = set()
        self._inflight_agent: set[str] = set()

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def assign_and_enqueue(self, pending: PendingEvent) -> EnqueueResult:
        async with self._lock:
            if (
                pending.turn_version is not None
                and pending.turn_version < self.active_turn_version
            ):
                return EnqueueResult(False, reason="stale_discard")

            if pending.envelope is None and pending.payload:
                wrapped = wrap_external_input(
                    _ingress_source_for_event(pending.source),
                    pending.payload,
                    ingest_turn_version=pending.turn_version or self.active_turn_version,
                    ingest_seq=self._ingest_seq + 1,
                )
                pending.envelope = wrapped
                pending.payload = {
                    **wrapped.payload,
                    "envelope": wrapped.to_dict(),
                    "normalized_payload_hash": wrapped.normalized_payload_hash,
                    "raw_payload_hash": wrapped.raw_payload_hash,
                }

            self._event_seq += 1
            self._ingest_seq += 1
            pending.event_seq = self._event_seq
            if not pending.event_id or len(pending.event_id) > 20:
                pending.event_id = f"{self.session_id}-e{self._event_seq:06d}"
            if pending.envelope:
                pending.ingest_seq = pending.envelope.ingest_seq

            if self._queue.full():
                prio = _SOURCE_MAP.get(pending.source, SourcePriority.ORCHESTRATOR)
                if prio >= SourcePriority.UI:
                    return EnqueueResult(False, reason="queue_full")
                await self._queue.put(pending)
                return EnqueueResult(True, pending)

            try:
                self._queue.put_nowait(pending)
            except asyncio.QueueFull:
                return EnqueueResult(False, reason="queue_full")
            return EnqueueResult(True, pending)

    async def dequeue(self) -> PendingEvent | None:
        return await self._queue.get()

    def to_runtime_event(self, pending: PendingEvent) -> RuntimeEvent:
        return RuntimeEvent(
            type=pending.type,
            session_id=pending.session_id,
            payload=pending.payload,
            turn_id=pending.turn_id,
            turn_version=pending.turn_version,
            source=pending.source,
            event_id=pending.event_id,
            event_seq=pending.event_seq,
            ingest_seq=pending.ingest_seq,
            normalized_payload_hash=(
                pending.envelope.normalized_payload_hash if pending.envelope else None
            ),
            raw_payload_hash=(
                pending.envelope.raw_payload_hash if pending.envelope else None
            ),
            is_deferred=pending.is_deferred,
        )

    def mark_inflight_start(self) -> None:
        self.inflight_event_count += 1

    def mark_inflight_end(self) -> None:
        self.inflight_event_count = max(0, self.inflight_event_count - 1)

    async def shutdown(self) -> None:
        await self._queue.put(None)


def _ingress_source_for_event(source: EventSource) -> IngressSource:
    mapping = {
        EventSource.UI: IngressSource.UI,
        EventSource.VOICE: IngressSource.VOICE,
        EventSource.RETRIEVAL: IngressSource.RETRIEVAL,
        EventSource.AGENT: IngressSource.LLM,
        EventSource.SYSTEM: IngressSource.SYSTEM,
        EventSource.ORCHESTRATOR: IngressSource.SYSTEM,
    }
    return mapping.get(source, IngressSource.SYSTEM)
