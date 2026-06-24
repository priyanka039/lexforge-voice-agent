"""Tests for event queue and sequencer (R1, R5)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.event_queue import EventSequencer, PendingEvent
from backend.runtime import EventSource


def test_concurrent_enqueue_strict_seq():
    async def run():
        seq = EventSequencer("sess-1")
        seq.active_turn_version = 1

        async def enqueue_one(i: int):
            p = PendingEvent(
                type="user_text_submit",
                session_id="sess-1",
                payload={"text": f"turn {i}"},
                turn_version=1,
                source=EventSource.UI,
            )
            return await seq.assign_and_enqueue(p)

        results = await asyncio.gather(*[enqueue_one(i) for i in range(20)])
        accepted = [r.event.event_seq for r in results if r.accepted and r.event]
        assert accepted == list(range(1, 21))

    asyncio.run(run())


def test_stale_discard():
    async def run():
        seq = EventSequencer("sess-1")
        seq.active_turn_version = 5
        p = PendingEvent(
            type="user_text_submit",
            session_id="sess-1",
            payload={"text": "old"},
            turn_version=3,
            source=EventSource.UI,
        )
        result = await seq.assign_and_enqueue(p)
        assert not result.accepted
        assert result.reason == "stale_discard"

    asyncio.run(run())
