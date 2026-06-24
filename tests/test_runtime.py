"""Tests for runtime two-phase dispatch."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.effects import DEFAULT_EFFECT_DECLARATIONS, EffectRunner
from backend.runtime import EventSource, RuntimeEvent, RuntimeState, TransitionEngine


def test_plan_dispatch_without_commit():
    registry = {d.name: d for d in DEFAULT_EFFECT_DECLARATIONS}
    runner = EffectRunner(registry, {})
    engine = TransitionEngine("s1", effect_graph_hash=runner.effect_graph_hash)
    engine.force_state(RuntimeState.AWAITING_USER)
    engine.begin_turn("t1")
    event = RuntimeEvent(
        type="user_text_submit",
        session_id="s1",
        payload={"text": "My lord"},
        turn_id="t1",
        turn_version=1,
        source=EventSource.UI,
    )
    plan = engine.plan_dispatch(event, terms_digest="abc")
    assert plan.planned
    assert plan.plan is not None
    assert plan.plan.to_state == RuntimeState.AWAITING_USER
    assert engine.state == RuntimeState.AWAITING_USER


def test_snapshot_includes_queue_metrics():
    registry = {d.name: d for d in DEFAULT_EFFECT_DECLARATIONS}
    runner = EffectRunner(registry, {})
    engine = TransitionEngine("s1", effect_graph_hash=runner.effect_graph_hash)
    event = RuntimeEvent(type="ping", session_id="s1", payload={})
    snap = engine.build_snapshot(event, queue_depth=3, inflight_event_count=1, terms_digest="v1")
    assert snap.queue_depth == 3
    assert snap.inflight_event_count == 1
    assert snap.terms_digest == "v1"
    assert snap.effect_graph_hash == runner.effect_graph_hash
