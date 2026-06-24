"""Session persistence with canonical JSON (Phase 2)."""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import Optional

from .canonical import canonical_copy
from .config import get_settings
from .state import MootCourtState

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DIR = os.path.join(os.path.dirname(_HERE), "sessions")


def _default_sessions_dir() -> str:
    custom = get_settings().sessions_dir
    return custom if custom else _DEFAULT_DIR


class SessionStore:
    def __init__(self, directory: str | None = None):
        self.directory = directory or _default_sessions_dir()
        os.makedirs(self.directory, exist_ok=True)

    def _json_path(self, session_id: str) -> str:
        return os.path.join(self.directory, f"{session_id}.json")

    def _md_path(self, session_id: str) -> str:
        return os.path.join(self.directory, f"{session_id}.md")

    def save(self, state: MootCourtState) -> None:
        if not state.transcript:
            return
        payload = canonical_copy(state.to_dict())
        json_path = self._json_path(state.session_id)
        try:
            fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, json_path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            with open(self._md_path(state.session_id), "w", encoding="utf-8") as f:
                f.write(state.to_markdown())
        except Exception:
            pass

    def load_json(self, session_id: str) -> Optional[dict]:
        p = self._json_path(session_id)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def markdown_path(self, session_id: str) -> Optional[str]:
        p = self._md_path(session_id)
        return p if os.path.exists(p) else None
