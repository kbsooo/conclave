"""Tests for voting mechanism."""

import math

from conclave.models import MeetingConfig, MeetingState, AgentConfig, TerminationMode
from conclave.vote import VoteManager


def test_supermajority_threshold():
    """2/3 supermajority: need ceil(n * 2/3) votes."""
    vm = VoteManager(TerminationMode.SUPERMAJORITY_VOTE, ["a", "b", "c"])

    # 3 agents → need 2
    assert vm.should_terminate({"a": True, "b": True, "c": False}) is True
    assert vm.should_terminate({"a": True, "b": False, "c": False}) is False


def test_supermajority_larger_group():
    """5 agents → need ceil(10/3) = 4 votes."""
    ids = ["a", "b", "c", "d", "e"]
    vm = VoteManager(TerminationMode.SUPERMAJORITY_VOTE, ids)

    threshold = math.ceil(5 * 2 / 3)  # 4
    assert threshold == 4

    votes_3 = {k: (i < 3) for i, k in enumerate(ids)}  # 3 yes
    votes_4 = {k: (i < 4) for i, k in enumerate(ids)}  # 4 yes

    assert vm.should_terminate(votes_3) is False
    assert vm.should_terminate(votes_4) is True


def test_task_completion_requires_unanimous():
    vm = VoteManager(TerminationMode.TASK_COMPLETION, ["a", "b", "c"])

    assert vm.should_terminate({"a": True, "b": True, "c": True}) is True
    assert vm.should_terminate({"a": True, "b": True, "c": False}) is False


def test_hard_limit():
    config = MeetingConfig(
        meeting_id="test",
        topic="Test",
        max_rounds=3,
        agents=[AgentConfig(agent_id="a", owner_id="o", persona="p")],
    )
    state = MeetingState(config=config, current_round=3)
    vm = VoteManager(TerminationMode.SUPERMAJORITY_VOTE, ["a"])

    assert vm.check_hard_limits(state) is True
