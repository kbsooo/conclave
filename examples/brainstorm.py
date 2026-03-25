"""Solo brainstorming example — one person, three agent perspectives."""

#%%
import asyncio
import logging

from conclave import (
    AgentConfig,
    MeetingConfig,
    MeetingOrchestrator,
    TerminationMode,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

#%%
config = MeetingConfig(
    meeting_id="brainstorm-001",
    topic="What side project should I build next?",
    context=(
        "I'm a solo developer with 2 months of free time. "
        "I want something that could grow into a real product. "
        "I know Python, TypeScript, and have experience with LLMs."
    ),
    termination=TerminationMode.SUPERMAJORITY_VOTE,
    max_rounds=5,
    agents=[
        AgentConfig(
            agent_id="visionary",
            owner_id="me",
            persona=(
                "You are the visionary thinker. You push for ambitious, novel ideas "
                "that could be genuinely impactful. You dislike incremental improvements "
                "and prefer moonshots. You get excited about emerging tech."
            ),
            model="openai/gpt-4o-mini",
        ),
        AgentConfig(
            agent_id="pragmatist",
            owner_id="me",
            persona=(
                "You are the pragmatist. You care about feasibility, time-to-market, "
                "and whether something can actually ship in 2 months. You push back on "
                "ideas that are too complex and favor MVPs with clear value."
            ),
            model="openai/gpt-4o-mini",
        ),
        AgentConfig(
            agent_id="critic",
            owner_id="me",
            persona=(
                "You are the devil's advocate. You find weaknesses in every idea — "
                "market saturation, technical risks, lack of differentiation. "
                "You're not negative for its own sake; you genuinely want to find "
                "the idea that survives scrutiny."
            ),
            model="openai/gpt-4o-mini",
        ),
    ],
)

#%%
async def main():
    result = await MeetingOrchestrator(config).run()

    print("\n" + "=" * 60)
    print("MEETING MINUTES")
    print("=" * 60)
    print(result.minutes.summary)

    if result.minutes.key_points:
        print("\nKey Points:")
        for point in result.minutes.key_points:
            print(f"  - {point}")

    if result.minutes.decisions:
        print("\nDecisions:")
        for decision in result.minutes.decisions:
            print(f"  - {decision}")

    print(f"\n[Ended: {result.termination_reason}]")
    print(f"[Rounds: {len(set(m.round_number for m in result.transcript if m.role == 'agent'))}]")

#%%
if __name__ == "__main__":
    asyncio.run(main())
