"""Pre-meeting briefing — 1-on-1 prep between user and their agent.

Before the conclave is sealed, each participant has an interactive
conversation with their agent to prepare. The agent asks questions,
the user answers, and the result becomes an enriched instruction
that the agent carries into the meeting.
"""

from __future__ import annotations

from conclave.backend import create_backend
from conclave.models import AgentConfig, MeetingConfig


BRIEFING_PROMPT = """\
You are about to attend a meeting on behalf of your principal.

Meeting topic: {topic}
Meeting context: {context}
Your role/instruction: {instruction}

Have a brief preparation conversation with your principal (the person you represent).
Ask ONE focused question at a time to understand:
- What outcomes they want from this meeting
- Any strong opinions or non-negotiables they have
- What they would consider a successful result

Be concise and conversational. Start with your first question now."""

FOLLOWUP_PROMPT = """\
You are preparing for a meeting on behalf of your principal.

Meeting topic: {topic}
Your role/instruction: {instruction}

Conversation so far:
{conversation}

Based on the conversation, either:
- Ask ONE more focused follow-up question, OR
- If you have enough information, say so and briefly summarize what you've learned.

Be concise."""

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

    # Show meeting context to the user first
    print(f"\n{'═' * 50}")
    print(f"  Briefing: {agent_config.agent_id}")
    print(f"  Topic: {meeting_config.topic}")
    if agent_config.instruction:
        print(f"  Role: {agent_config.instruction}")
    print(f"{'═' * 50}")
    print(f"  (type 'done' to finish, 'skip' to skip this agent)")

    # Get first question from agent
    initial_prompt = BRIEFING_PROMPT.format(
        topic=meeting_config.topic,
        context=meeting_config.context or "(no additional context)",
        instruction=agent_config.instruction or "(no specific instruction)",
    )

    agent_message = await backend.generate(initial_prompt)
    conversation: list[str] = []

    if not agent_message.strip():
        print("\n  ⚠ Agent returned empty response, skipping briefing.")
        return agent_config.instruction

    conversation.append(f"Agent: {agent_message}")

    for i in range(max_exchanges):
        # Show agent's question
        print(f"\n🤖 {agent_message}")

        # Get user's answer
        user_input = _read_user_input()
        if not user_input or user_input.lower() in ("skip", "done", "pass"):
            break

        conversation.append(f"User: {user_input}")

        # Agent responds with next question (or wraps up)
        if i < max_exchanges - 1:
            followup_prompt = FOLLOWUP_PROMPT.format(
                topic=meeting_config.topic,
                instruction=agent_config.instruction or "(none)",
                conversation="\n".join(conversation),
            )
            agent_message = await backend.generate(followup_prompt)
            if not agent_message.strip():
                break
            conversation.append(f"Agent: {agent_message}")

    # Consolidate into enriched instruction
    if not any(line.startswith("User:") for line in conversation):
        # User didn't say anything — keep original instruction
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
        return input("\nYou: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
