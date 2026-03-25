"""Meeting orchestrator — the sealed conclave engine.

Once `run()` is called, no human input is accepted until the meeting concludes.
The orchestrator owns the MeetingState and coordinates all modules.
"""

from __future__ import annotations

import logging

from conclave.agent import Agent
from conclave.llm import LLMClient
from conclave.models import (
    MeetingConfig,
    MeetingResult,
    MeetingState,
    MeetingStatus,
    Message,
)
from conclave.output import OutputGenerator
from conclave.turn import ShuffledRoundRobin, TurnStrategy
from conclave.vote import VoteManager

logger = logging.getLogger(__name__)


class MeetingOrchestrator:
    """Runs a sealed meeting from start to finish.

    Usage:
        config = MeetingConfig(...)
        result = await MeetingOrchestrator(config).run()
    """

    def __init__(
        self,
        config: MeetingConfig,
        llm: LLMClient | None = None,
        turn_strategy: TurnStrategy | None = None,
    ) -> None:
        self._llm = llm or LLMClient()
        self._state = MeetingState(config=config)

        # Create agents — each holds its own persona privately
        self._agents: dict[str, Agent] = {
            ac.agent_id: Agent(
                config=ac,
                meeting_topic=config.topic,
                meeting_context=config.context,
                llm=self._llm,
            )
            for ac in config.agents
        }

        self._turn_strategy = turn_strategy or ShuffledRoundRobin()
        self._vote_manager = VoteManager(
            mode=config.termination,
            agent_ids=[a.agent_id for a in config.agents],
        )
        self._output_generator = OutputGenerator(llm=self._llm)

    async def run(self) -> MeetingResult:
        """Execute the entire meeting. Returns when done.

        ┌─────────────────────────────────────────┐
        │  THE SEALED CONCLAVE                    │
        │  No human input past this point.        │
        └─────────────────────────────────────────┘
        """
        self._state.status = MeetingStatus.IN_PROGRESS

        # Opening system message
        self._add_system_message(
            f"Meeting started. Topic: {self._state.config.topic}"
        )

        logger.info(
            "Conclave sealed: %d agents, max %d rounds, termination=%s",
            len(self._agents),
            self._state.config.max_rounds,
            self._state.config.termination.value,
        )

        # ── Main loop: rounds → turns → votes ─────────────────────────
        while not self._vote_manager.check_hard_limits(self._state):
            self._state.current_round += 1
            should_continue = await self._run_round()
            if not should_continue:
                break

        # Set termination reason if hit hard limit
        if self._state.termination_reason is None:
            self._state.termination_reason = f"Hard limit reached: {self._state.config.max_rounds} rounds"

        self._state.status = MeetingStatus.COMPLETED

        logger.info(
            "Conclave concluded after %d rounds: %s (total tokens: %d)",
            self._state.current_round,
            self._state.termination_reason,
            self._llm.total_tokens,
        )

        return await self._generate_outputs()

    async def _run_round(self) -> bool:
        """Execute one round. Returns True if meeting should continue."""
        round_num = self._state.current_round

        # Get shuffled turn order for this round
        turn_order = self._turn_strategy.get_round_order(self._state)

        logger.info("Round %d — turn order: %s", round_num, turn_order)

        # Each agent speaks exactly once per round
        for agent_id in turn_order:
            agent = self._agents[agent_id]
            utterance = await agent.speak(self._state.transcript, round_num)

            self._state.transcript.append(Message(
                role="agent",
                agent_id=agent_id,
                content=utterance,
                round_number=round_num,
            ))

            logger.debug("[%s] %s", agent_id, utterance[:100])

        # End-of-round voting
        votes = await self._vote_manager.collect_votes(
            self._agents, self._state.transcript,
        )
        self._state.votes = votes

        yes_count = sum(1 for v in votes.values() if v)
        logger.info(
            "Round %d votes: %d/%d want to end",
            round_num, yes_count, len(votes),
        )

        if self._vote_manager.should_terminate(votes):
            self._state.termination_reason = (
                f"Vote passed ({yes_count}/{len(votes)}) after round {round_num}"
            )
            return False  # meeting ends

        return True  # continue

    async def _generate_outputs(self) -> MeetingResult:
        """Post-meeting: generate minutes and personal reports."""
        # Shared minutes (neutral, no persona)
        minutes = await self._output_generator.generate_minutes(self._state)

        # Per-agent personal reports (persona-informed)
        personal_reports = {}
        for agent in self._agents.values():
            report = await self._output_generator.generate_personal_report(
                self._state, agent,
            )
            personal_reports[agent.owner_id] = report

        return MeetingResult(
            meeting_id=self._state.config.meeting_id,
            status=self._state.status,
            termination_reason=self._state.termination_reason or "Unknown",
            transcript=self._state.transcript,
            minutes=minutes,
            personal_reports=personal_reports,
        )

    def _add_system_message(self, content: str) -> None:
        self._state.transcript.append(Message(
            role="system",
            content=content,
            round_number=self._state.current_round,
        ))
