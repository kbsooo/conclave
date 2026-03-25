"""Pre-meeting briefing — 1-on-1 prep between user and their agent.

Before the conclave is sealed, each participant has an interactive
conversation with their agent to prepare. The agent asks questions,
the user answers, and the result becomes an enriched instruction
that the agent carries into the meeting.
"""

from __future__ import annotations

import sys

from conclave.backend import Backend, create_backend
from conclave.models import AgentConfig, MeetingConfig


BRIEFING_PROMPT = """\
You are about to attend a meeting on behalf of your principal.

Meeting topic: {topic}
Meeting context: {context}
Your initial instruction: {instruction}

Your task now is to have a brief preparation conversation with your principal \
(the person you represent). Ask 2-3 focused questions to understand:
- What outcomes they want from this meeting
- Any strong opinions or non-negotiables they have
- What they would consider a successful result

Be concise and conversational. Ask ONE question at a time.
Start with your first question now."""

CONSOLIDATION_PROMPT = """\
Based on the preparation conversation below, write a consolidated briefing \
that captures everything you learned about your principal's priorities for this meeting.

Meeting topic: {topic}
Original instruction: {instruction}

Conversation:
{conversation}

Write the briefing as a clear, actionable set of instructions for yourself \
as you enter the meeting. Include specific priorities, positions, and any \
red lines mentioned. Be concise — this will be your guide during the meeting.
Output ONLY the briefing text, nothing else."""


async def brief_agent(
    agent_config: AgentConfig,
    meeting_config: MeetingConfig,
    max_exchanges: int = 3,
) -> str:
    """Run an interactive briefing session for one agent.

    Returns the enriched instruction string.
    """
    backend = create_backend(
        backend_type=agent_config.backend,
        command=agent_config.command,
        model=agent_config.model,
        temperature=agent_config.temperature,
        cli_args=agent_config.cli_args,
        cli_timeout=agent_config.cli_timeout,
    )

    # Start the briefing conversation
    initial_prompt = BRIEFING_PROMPT.format(
        topic=meeting_config.topic,
        context=meeting_config.context or "(no additional context)",
        instruction=agent_config.instruction or "(no specific instruction)",
    )

    conversation: list[str] = []
    agent_message = await backend.generate(initial_prompt)
    conversation.append(f"Agent: {agent_message}")

    print(f"\n{'─' * 50}")
    print(f"Briefing: {agent_config.agent_id}")
    print(f"{'─' * 50}")

    for i in range(max_exchanges):
        # Show agent's question
        print(f"\n🤖 {agent_message}")

        # Get user's answer
        print()
        user_input = _read_user_input()
        if not user_input or user_input.lower() in ("skip", "done", "pass"):
            break

        conversation.append(f"User: {user_input}")

        # Agent responds with next question (or wraps up)
        if i < max_exchanges - 1:
            followup_prompt = (
                f"{initial_prompt}\n\n"
                f"Conversation so far:\n" + "\n".join(conversation) + "\n\n"
                f"Ask your next question (or say you have enough information)."
            )
            agent_message = await backend.generate(followup_prompt)
            conversation.append(f"Agent: {agent_message}")

    # Consolidate into enriched instruction
    if len(conversation) <= 1:
        # User skipped — keep original instruction
        return agent_config.instruction

    consolidated = await backend.generate(
        CONSOLIDATION_PROMPT.format(
            topic=meeting_config.topic,
            instruction=agent_config.instruction or "(none)",
            conversation="\n".join(conversation),
        )
    )

    print(f"\n✅ Briefing complete for {agent_config.agent_id}")
    return consolidated


async def brief_all_agents(
    config: MeetingConfig,
    max_exchanges: int = 3,
) -> MeetingConfig:
    """Run briefing sessions for all agents, return updated config.

    Each agent's instruction is replaced with the enriched briefing.
    """
    updated_agents: list[AgentConfig] = []

    for ac in config.agents:
        enriched_instruction = await brief_agent(ac, config, max_exchanges)
        updated = ac.model_copy(update={"instruction": enriched_instruction})
        updated_agents.append(updated)

    return config.model_copy(update={"agents": updated_agents})


def _read_user_input() -> str:
    """Read user input from stdin. Handles EOF gracefully."""
    try:
        return input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
