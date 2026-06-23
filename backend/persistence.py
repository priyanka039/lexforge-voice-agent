"""Session persistence.

Saves each moot-court session to disk as both machine-readable JSON and a
human-readable Markdown transcript. This is the seam to plug into an existing
"Matter" system later: swap `SessionStore` for one that writes to your matter
store / database.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .state import MootCourtState

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DIR = os.path.join(os.path.dirname(_HERE), "sessions")


class SessionStore:
    def __init__(self, directory: str = _DEFAULT_DIR):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def _json_path(self, session_id: str) -> str:
        return os.path.join(self.directory, f"{session_id}.json")

    def _md_path(self, session_id: str) -> str:
        return os.path.join(self.directory, f"{session_id}.md")

    def save(self, state: MootCourtState) -> None:
        # ignore empty sessions (nothing argued yet)
        if not state.transcript:
            return
        try:
            with open(self._json_path(state.session_id), "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
            with open(self._md_path(state.session_id), "w", encoding="utf-8") as f:
                f.write(state.to_markdown())
        except Exception:
            # persistence must never crash a live session
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
