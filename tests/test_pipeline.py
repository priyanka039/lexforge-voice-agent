"""End-to-end pipeline tests using the offline stub brain (no API keys needed)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import Settings
from backend.orchestrator import EventType, Orchestrator, _split_sentences
from backend.retrieval.seed_corpus import SeedCorpusRetriever
from backend.state import BenchTemperament


def _stub_settings() -> Settings:
    s = Settings()
    s.openai_api_key = ""   # force the offline stub brain
    s.gemini_api_key = ""
    # offline: seed corpus only (no network during tests)
    s.enable_wikipedia = False
    s.enable_web_search = False
    s.enable_indiankanoon_scrape = False
    s.indiankanoon_api_token = ""
    return s


def test_sentence_splitter():
    out = _split_sentences("Counsel, your point is noted. On what do you rely? Indeed!")
    assert out == ["Counsel, your point is noted.", "On what do you rely?", "Indeed!"]


def test_seed_corpus_finds_kesavananda():
    async def run():
        r = SeedCorpusRetriever()
        res = await r.search("basic structure doctrine constitutional amendment", limit=3)
        return res
    res = asyncio.run(run())
    titles = [x.title for x in res]
    assert any("Kesavananda" in t for t in titles)


def test_pipeline_judge_speaks():
    async def run():
        orch = Orchestrator(_stub_settings())
        events = []
        async for ev in orch.handle_turn("My lord, I submit the amendment is valid."):
            events.append(ev)
        await orch.close()
        return events
    events = asyncio.run(run())
    types = [e.type for e in events]
    assert EventType.INTENT in types
    assert EventType.SPEAK_SENTENCE in types
    assert EventType.TURN_DONE in types
    # the judge produced at least one spoken sentence
    spoken = [e for e in events if e.type == EventType.SPEAK_SENTENCE]
    assert spoken and any(e.data.get("text") for e in spoken)


def test_pipeline_citation_path_parses():
    async def run():
        orch = Orchestrator(_stub_settings())
        events = []
        async for ev in orch.handle_turn(
                "I rely on air nineteen seventy three sc fourteen sixty one."):
            events.append(ev)
        await orch.close()
        return events
    events = asyncio.run(run())
    # a note event should carry the normalised citation
    cites = []
    for e in events:
        if e.type == EventType.NOTE and e.data.get("citations"):
            cites += e.data["citations"]
    assert "AIR 1973 SC 1461" in cites


def test_feedback_runs():
    async def run():
        orch = Orchestrator(_stub_settings())
        async for _ in orch.handle_turn("I submit the petition is maintainable."):
            pass
        events = []
        async for ev in orch.run_feedback():
            events.append(ev)
        await orch.close()
        return events
    events = asyncio.run(run())
    assert any(e.type == EventType.FEEDBACK for e in events)


def test_bench_temperament_in_context():
    orch = Orchestrator(_stub_settings())
    orch.state.bench_temperament = BenchTemperament.HOT
    assert "hot" in orch.state.context_block().lower()
