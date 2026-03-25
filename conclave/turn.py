"""Turn strategy — decides who speaks next.

Default: round-robin with per-round shuffle.
Every agent speaks exactly once per round. Shuffle prevents positional bias.
This structurally guarantees the "all agents must participate" constraint.
"""

from __future__ import annotations

import random
from typing import Protocol

from conclave.models import MeetingState


class TurnStrategy(Protocol):
    """Interface for turn order strategies."""

    def get_round_order(self, state: MeetingState) -> list[str]:
        """Return ordered list of agent_ids for the current round."""
        ...


class ShuffledRoundRobin:
    """Each round, every agent speaks once in a shuffled order.

    - Guarantees all agents participate (structural, not policy-based)
    - Shuffle prevents first-mover / last-mover advantage
    - Later speakers in a round see what earlier speakers said (natural flow)
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def get_round_order(self, state: MeetingState) -> list[str]:
        agent_ids = [a.agent_id for a in state.config.agents]
        self._rng.shuffle(agent_ids)
        return agent_ids
