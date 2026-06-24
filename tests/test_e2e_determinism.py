"""End-to-end determinism replay (Phase 9)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import Settings
from backend.replay import (
    ReplaySnapshot,
    capture_snapshot,
    load_event_log,
    run_replay,
    strip_nondeterministic,
)
from backend.session_runtime import SessionRuntime

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "e2e"
_GOLDEN = _FIXTURES / "golden_snapshot.json"


def _stub_settings() -> Settings:
    s = Settings()
    s.openai_api_key = ""
    s.gemini_api_key = ""
    s.enable_wikipedia = False
    s.enable_web_search = False
    s.enable_indiankanoon_scrape = False
    return s


def _assert_snapshots_equal(a: ReplaySnapshot, b: ReplaySnapshot) -> None:
    assert a.session_id == b.session_id
    assert a.transition_digest == b.transition_digest, (
        f"transition_digest mismatch:\n  a={a.transition_digest}\n  b={b.transition_digest}"
    )
    assert a.ledger_digest == b.ledger_digest, (
        f"ledger_digest mismatch:\n  a={a.ledger_digest}\n  b={b.ledger_digest}"
    )
    assert a.authority_set_digest == b.authority_set_digest, (
        f"authority_set_digest mismatch:\n  a={a.authority_set_digest}\n  b={b.authority_set_digest}"
    )
    assert a.vocab_digest == b.vocab_digest
    assert a.session_canonical_digest == b.session_canonical_digest, (
        "session canonical digest mismatch"
    )
    assert a.session_canonical == b.session_canonical


def test_e2e_replay_twice_identical():
    """Run the same event log twice — all digests must match."""
    turns = load_event_log()
    settings = _stub_settings()

    async def run():
        snap_a = await run_replay(settings, turns, session_id="e2e-replay-01")
        snap_b = await run_replay(settings, turns, session_id="e2e-replay-01")
        return snap_a, snap_b

    a, b = asyncio.run(run())
    _assert_snapshots_equal(a, b)
    assert a.transition_digest
    assert a.ledger_digest
    assert len(a.session_canonical.get("transcript", [])) >= 2


def test_e2e_golden_snapshot_regression():
    """Replay must match committed golden digests (update fixture if intentional change)."""
    turns = load_event_log()
    snap = asyncio.run(run_replay(_stub_settings(), turns, session_id="e2e-replay-01"))

    if not _GOLDEN.exists():
        payload = {
            "transition_digest": snap.transition_digest,
            "ledger_digest": snap.ledger_digest,
            "authority_set_digest": snap.authority_set_digest,
            "vocab_digest": snap.vocab_digest,
            "session_canonical_digest": snap.session_canonical_digest,
        }
        _GOLDEN.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return

    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert snap.transition_digest == golden["transition_digest"]
    assert snap.ledger_digest == golden["ledger_digest"]
    assert snap.authority_set_digest == golden["authority_set_digest"]
    assert snap.vocab_digest == golden["vocab_digest"]
    assert snap.session_canonical_digest == golden["session_canonical_digest"]


def test_strip_nondeterministic_removes_timestamps():
    raw = {"started_at": 1.0, "transcript": [{"ts": 2.0, "text": "hi"}]}
    stripped = strip_nondeterministic(raw)
    assert "started_at" not in stripped
    assert "ts" not in stripped["transcript"][0]
    assert stripped["transcript"][0]["text"] == "hi"


def test_capture_snapshot_from_runtime():
    async def run():
        outbound = []
        rt = SessionRuntime(_stub_settings())
        rt.state.session_id = "cap-snap-01"
        await rt.start(outbound.append)
        from backend.runtime import EventSource
        await rt.enqueue_user_text("My lord, the petition is maintainable.", source=EventSource.UI)
        await asyncio.sleep(0.4)
        snap = capture_snapshot(rt)
        await rt.close()
        return snap

    snap = asyncio.run(run())
    assert snap.transition_digest
    assert snap.session_canonical_digest
