"""Solo brainstorming example — one person, three agent perspectives.

CLI backend: each agent uses `claude` CLI which already has memory about you.
API backend: uncomment the api examples to use raw API calls with explicit personas.
"""

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
# ── CLI backend (primary): agents already know you via their memory ────
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
            backend="cli",
            command="claude",
            instruction="Push for ambitious, novel ideas. Prefer moonshots over incremental improvements.",
        ),
        AgentConfig(
            agent_id="pragmatist",
            owner_id="me",
            backend="cli",
            command="claude",
            instruction="Focus on feasibility and time-to-market. Favor MVPs that can ship in 2 months.",
        ),
        AgentConfig(
            agent_id="critic",
            owner_id="me",
            backend="cli",
            command="claude",
            instruction="Find weaknesses in every idea — market saturation, technical risks, lack of differentiation.",
        ),
    ],
)

# ── API backend alternative (needs explicit persona, no memory) ────────
# config_api = MeetingConfig(
#     meeting_id="brainstorm-api",
#     topic="What side project should I build next?",
#     context="Solo dev, 2 months, Python/TS/LLM experience.",
#     termination=TerminationMode.SUPERMAJORITY_VOTE,
#     max_rounds=5,
#     agents=[
#         AgentConfig(
#             agent_id="visionary",
#             owner_id="me",
#             backend="api",
#             model="openai/gpt-4o-mini",
#             persona="You are a visionary thinker who pushes for ambitious, novel ideas.",
#         ),
#         AgentConfig(
#             agent_id="pragmatist",
#             owner_id="me",
#             backend="api",
#             model="anthropic/claude-sonnet-4-20250514",
#             persona="You are a pragmatist who cares about feasibility and shipping fast.",
#         ),
#     ],
# )

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
