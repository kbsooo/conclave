"""Tests for turn strategy."""

from conclave.models import AgentConfig, MeetingConfig, MeetingState
from conclave.turn import ShuffledRoundRobin


def _make_state(n_agents: int = 3) -> MeetingState:
    config = MeetingConfig(
        meeting_id="test",
        topic="Test",
        agents=[
            AgentConfig(agent_id=f"a{i}", owner_id=f"o{i}", persona=f"p{i}")
            for i in range(n_agents)
        ],
    )
    return MeetingState(config=config)


def test_all_agents_speak():
    """Every agent must appear exactly once per round."""
    strategy = ShuffledRoundRobin(seed=42)
    state = _make_state(3)
    order = strategy.get_round_order(state)

    assert sorted(order) == ["a0", "a1", "a2"]
    assert len(order) == 3


def test_shuffle_varies_order():
    """Different rounds should (usually) have different orders."""
    strategy = ShuffledRoundRobin(seed=None)
    state = _make_state(5)

    orders = [tuple(strategy.get_round_order(state)) for _ in range(10)]
    # With 5 agents and 10 rounds, extremely unlikely all are the same
    assert len(set(orders)) > 1


def test_deterministic_with_seed():
    """Same seed should produce the same first shuffle."""
    state = _make_state(4)
    order1 = ShuffledRoundRobin(seed=123).get_round_order(state)
    order2 = ShuffledRoundRobin(seed=123).get_round_order(state)
    assert order1 == order2
