"""Central configuration. All secrets/tunables come from environment variables.

Load order: a local `.env` file (via python-dotenv) then real environment.
Nothing in here is secret on its own; the `.env` file holds the actual keys.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

# Load .env from project root (parent of backend/) and from cwd, if present.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
load_dotenv(os.path.join(_ROOT, ".env"))
load_dotenv()  # also pick up a .env in the current working directory


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- Gemini Live (voice only: STT + TTS) ---
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    # Live API model IDs change frequently. Default to a known-good model for the
    # Google AI (Generative Language) API; override via GEMINI_LIVE_MODEL.
    gemini_live_model: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-09-2025"
        )
    )
    gemini_live_model_fallbacks: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_LIVE_MODEL_FALLBACKS",
            "gemini-2.5-flash-native-audio-preview-12-2025,"
            "gemini-2.5-flash-native-audio-latest,"
            "gemini-3.1-flash-live-preview",
        )
    )
    # Voice for TTS. See Gemini docs for the list (Puck, Charon, Kore, Fenrir, Aoede, ...).
    tts_voice: str = field(default_factory=lambda: os.getenv("GEMINI_TTS_VOICE", "Charon"))
    # Audio formats mandated by the Live API.
    input_sample_rate: int = 16000   # what we send to Gemini STT
    output_sample_rate: int = 24000  # what Gemini TTS returns

    # --- Reasoning brain (OpenAI / any OpenAI-compatible endpoint) ---
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    # A fast model for the orchestrator/router, a stronger one for legal reasoning.
    router_model: str = field(default_factory=lambda: os.getenv("ROUTER_MODEL", "gpt-4o-mini"))
    reasoning_model: str = field(default_factory=lambda: os.getenv("REASONING_MODEL", "gpt-4o-mini"))

    # --- Precedent retrieval (all free by default; no token needed) ---
    indiankanoon_api_token: str = field(
        default_factory=lambda: os.getenv("INDIANKANOON_API_TOKEN", "")
    )
    # Free sources (on by default):
    enable_wikipedia: bool = field(
        default_factory=lambda: _get_bool("ENABLE_WIKIPEDIA", True)
    )
    enable_web_search: bool = field(
        # Back-compat: honour the old ENABLE_WEB_RETRIEVAL name too.
        default_factory=lambda: _get_bool("ENABLE_WEB_SEARCH",
                                          _get_bool("ENABLE_WEB_RETRIEVAL", True))
    )
    # Opt-in only: Indian Kanoon's robots.txt disallows /search/.
    enable_indiankanoon_scrape: bool = field(
        default_factory=lambda: _get_bool("ENABLE_INDIANKANOON_SCRAPE", False)
    )
    retrieval_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("RETRIEVAL_TIMEOUT_S", "8"))
    )

    # --- Server ---
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    # --- Behaviour tuning ---
    # Max spoken sentences any agent should produce (voice UX discipline).
    max_spoken_sentences: int = field(
        default_factory=lambda: int(os.getenv("MAX_SPOKEN_SENTENCES", "3"))
    )

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def gemini_model_path(self) -> str:
        m = self.gemini_live_model
        return m if m.startswith("models/") else f"models/{m}"

    @property
    def gemini_model_candidates(self) -> list[str]:
        """Primary model first, then fallbacks (deduped)."""
        raw = [self.gemini_live_model]
        raw += [x.strip() for x in self.gemini_live_model_fallbacks.split(",") if x.strip()]
        seen: set[str] = set()
        out: list[str] = []
        for m in raw:
            if m not in seen:
                seen.add(m)
                out.append(m if m.startswith("models/") else f"models/{m}")
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
