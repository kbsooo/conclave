# Conclave

A multi-agent meeting system where AI agents represent humans in meetings.

> Like a papal conclave — agents enter, the door seals, and no one intervenes until white smoke rises.

## Concept

Instead of attending meetings yourself, configure an AI agent with your persona, context, and priorities. Your agent joins the meeting alongside other participants' agents. Once the meeting starts, no human can intervene — agents discuss freely until they reach a conclusion.

### Key Features

- **Private personas** — Your agent knows your priorities and biases, but other participants never see them
- **LLM-agnostic** — Each agent can use a different model (GPT-4o, Claude, Gemini, Llama, etc.)
- **Multiple agents per person** — Send several agents with different perspectives (costs more tokens)
- **Structured termination** — Task-based meetings end on completion; brainstorming ends with 2/3 supermajority vote
- **Dual output** — Shared meeting minutes for everyone + private report for each participant

### How It Works

1. **Configure** — Define the meeting topic, shared context, and each agent's private persona
2. **Seal** — Start the meeting; no human input from this point
3. **Discuss** — Agents take turns speaking in shuffled rounds; every agent must participate
4. **Conclude** — Meeting ends when the goal is met or vote threshold is reached
5. **Report** — Shared minutes are distributed to all; each agent writes a private report for its owner

## Installation

```bash
pip install -e ".[dev]"
```

## Quick Start

```python
import asyncio
from conclave import MeetingOrchestrator, MeetingConfig, AgentConfig, TerminationMode

config = MeetingConfig(
    meeting_id="brainstorm-001",
    topic="What should we build next?",
    context="We have 3 engineers and 2 months. Focus on user growth.",
    termination=TerminationMode.SUPERMAJORITY_VOTE,
    max_rounds=10,
    agents=[
        AgentConfig(
            agent_id="eng-lead",
            owner_id="alice",
            persona="You represent Alice, the engineering lead. Push for technical feasibility.",
            model="claude-sonnet-4-20250514",
        ),
        AgentConfig(
            agent_id="product-mgr",
            owner_id="bob",
            persona="You represent Bob, the PM. Focus on user impact and growth metrics.",
            model="gpt-4o",
        ),
        AgentConfig(
            agent_id="designer",
            owner_id="carol",
            persona="You represent Carol, the designer. Advocate for UX quality.",
            model="claude-sonnet-4-20250514",
        ),
    ],
)

result = asyncio.run(MeetingOrchestrator(config).run())

# Shared meeting minutes
print(result.minutes.summary)

# Private report (only Carol sees this)
print(result.personal_reports["carol"].summary)
```

## Configuration (YAML)

```yaml
meeting_id: "roadmap-q2"
topic: "Q2 Feature Prioritization"
context: |
  Budget allows 2 of 3 features: real-time collab, mobile v2, enterprise SSO.
termination: supermajority_vote
max_rounds: 15
agents:
  - agent_id: "eng-lead"
    owner_id: "alice"
    persona: "You care about technical debt and push for SSO."
    model: "claude-sonnet-4-20250514"
  - agent_id: "product-mgr"
    owner_id: "bob"
    persona: "You are data-driven and push for mobile + real-time collab."
    model: "gpt-4o"
```

## License

MIT
