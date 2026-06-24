"""Integration test for SessionRuntime turn processing."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import Settings
from backend.runtime import EventSource
from backend.session_runtime import SessionRuntime


def _stub_settings() -> Settings:
    s = Settings()
    s.openai_api_key = ""
    s.gemini_api_key = ""
    s.enable_wikipedia = False
    s.enable_web_search = False
    s.enable_indiankanoon_scrape = False
    return s


def test_session_runtime_processes_user_turn():
    async def run():
        outbound = []
        rt = SessionRuntime(_stub_settings())
        await rt.start(outbound.append)
        ok = await rt.enqueue_user_text("My lord, I submit the amendment is valid.",
                                        source=EventSource.UI)
        assert ok
        await asyncio.sleep(0.5)
        await rt.close()
        types = [m.get("type") for m in outbound]
        assert "turn_done" in types or "speaking" in types
        assert rt.vocab.terms_digest
        return outbound

    asyncio.run(run())
