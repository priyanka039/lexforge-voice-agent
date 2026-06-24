"""R6 concurrency boundary: session state commits only via SessionRuntime."""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"


def _read(rel: str) -> str:
    return (_BACKEND / rel).read_text(encoding="utf-8")


def test_app_uses_session_runtime_not_direct_orchestrator_turns():
    app = _read("app.py")
    assert "SessionRuntime" in app
    assert "enqueue_user_text" in app
    # Turn handling must not bypass the event queue from the connection layer.
    assert not re.search(r"orchestrator\.handle_turn\s*\(", app)


def test_moot_court_state_commit_in_session_runtime_only():
    """MootCourtState assignment on live session should occur in session_runtime."""
    allowed = {"session_runtime.py", "effects.py", "orchestrator.py", "state.py", "persistence.py"}
    pattern = re.compile(r"\bself\.state\s*=\s*run_result\.staging\.session_state\b")
    for path in _BACKEND.rglob("*.py"):
        if path.name in allowed or path.name == "session_runtime.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), f"unexpected staging commit in {path.relative_to(_ROOT)}"


def test_transition_engine_state_is_fsm_only():
    """RuntimeState (FSM) mutations belong to transition engine, not MootCourtState."""
    rt = _read("runtime.py")
    assert "self.state = plan.to_state" in rt
    assert "MootCourtState" not in rt
