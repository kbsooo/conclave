"""Voting mechanism and termination detection.

Two modes:
- SUPERMAJORITY_VOTE: >= 2/3 of agents vote to end
- TASK_COMPLETION: ALL agents agree the task is done
Both are overridden by hard limits (max_rounds).
"""

from __future__ import annotations

import math

from conclave.agent import Agent
from conclave.models import Message, MeetingState, TerminationMode


class VoteManager:
    """Collects votes and determines if the meeting should end."""

    def __init__(self, mode: TerminationMode, agent_ids: list[str]) -> None:
        self.mode = mode
        self.agent_ids = agent_ids

    async def collect_votes(
        self, agents: dict[str, Agent], transcript: list[Message],
    ) -> dict[str, bool]:
        """Ask each agent whether the meeting should end."""
        votes: dict[str, bool] = {}
        for agent_id in self.agent_ids:
            votes[agent_id] = await agents[agent_id].vote_to_end(transcript)
        return votes

    def should_terminate(self, votes: dict[str, bool]) -> bool:
        """Check if votes meet the termination threshold."""
        yes_count = sum(1 for v in votes.values() if v)
        total = len(votes)

        if self.mode == TerminationMode.SUPERMAJORITY_VOTE:
            # 2/3 supermajority (like a real conclave)
            threshold = math.ceil(total * 2 / 3)
            return yes_count >= threshold
        else:
            # TASK_COMPLETION: unanimous agreement
            return yes_count == total

    def check_hard_limits(self, state: MeetingState) -> bool:
        """True if max_rounds reached. Always overrides votes."""
        return state.current_round >= state.config.max_rounds
