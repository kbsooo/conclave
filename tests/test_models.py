"""Tests for Pydantic data models."""

from conclave.models import (
    AgentConfig,
    MeetingConfig,
    MeetingState,
    MeetingStatus,
    Message,
    Minutes,
    TerminationMode,
)


def test_agent_config_cli_defaults():
    ac = AgentConfig(agent_id="a1", owner_id="alice")
    assert ac.backend == "cli"
    assert ac.command == "claude"
    assert ac.instruction == ""


def test_agent_config_api_backend():
    ac = AgentConfig(
        agent_id="a1", owner_id="alice",
        backend="api", persona="test persona", model="openai/gpt-4o",
    )
    assert ac.backend == "api"
    assert ac.persona == "test persona"
    assert ac.model == "openai/gpt-4o"
    assert ac.temperature == 0.7


def test_meeting_config_validation():
    config = MeetingConfig(
        meeting_id="test",
        topic="Test topic",
        agents=[
            AgentConfig(agent_id="a1", owner_id="alice", instruction="be bold"),
            AgentConfig(agent_id="a2", owner_id="bob", instruction="be careful"),
        ],
    )
    assert config.termination == TerminationMode.SUPERMAJORITY_VOTE
    assert config.max_rounds == 20
    assert len(config.agents) == 2


def test_meeting_state_initial():
    config = MeetingConfig(
        meeting_id="test",
        topic="Test",
        agents=[AgentConfig(agent_id="a1", owner_id="o1")],
    )
    state = MeetingState(config=config)
    assert state.status == MeetingStatus.PENDING
    assert state.current_round == 0
    assert state.transcript == []


def test_message_creation():
    msg = Message(role="agent", agent_id="a1", content="Hello", round_number=1)
    assert msg.role == "agent"
    assert msg.timestamp is not None


def test_minutes_defaults():
    m = Minutes(summary="Test summary")
    assert m.key_points == []
    assert m.decisions == []
    assert m.action_items == []
