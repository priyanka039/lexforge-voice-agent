"""Tests for agent schemas and advisor (Phase 4)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.advisor import AdvisorAgent, WeaknessAgent
from backend.agents.context import MAX_DIALOGUE_TURNS, build_bounded_context
from backend.agents.judge import JudgeAgent
from backend.agents.schema import JudgeOutput, WeaknessOutput
from backend.state import Intent, MootCourtState


def test_weakness_agent_alias():
    assert WeaknessAgent is AdvisorAgent


def test_judge_intervention_type_procedural():
    from backend.config import Settings
    agent = JudgeAgent(None, Settings())
    state = MootCourtState()
    assert agent._intervention_type(state, Intent.PROCEDURAL) == "procedural"
    assert agent._intervention_type(state, Intent.ARGUMENT) == "substantive"


def test_bounded_context_limits_dialogue():
    state = MootCourtState()
    from backend.state import Speaker
    for i in range(10):
        state.add_turn(Speaker.ADVOCATE, f"Turn {i}")
    ctx = build_bounded_context(state)
    assert ctx.count("ADVOCATE:") <= MAX_DIALOGUE_TURNS


def test_schema_roundtrip():
    w = WeaknessOutput(weaknesses=["gap in authority"]).to_structured()
    assert w["weaknesses"] == ["gap in authority"]
    j = JudgeOutput(intervention_type="substantive", question="On what do you rely?").to_structured()
    assert j["intervention_type"] == "substantive"
