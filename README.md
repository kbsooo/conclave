# Conclave

A multi-agent meeting system where AI agents represent humans in meetings.

> Like a papal conclave — agents enter, the door seals, and no one intervenes until white smoke rises.

## Concept

Instead of attending meetings yourself, configure an AI agent with your persona, context, and priorities. Your agent joins the meeting alongside other participants' agents. Once the meeting starts, no human can intervene — agents discuss freely until they reach a conclusion.

### Key Features

- **Private personas** — Your agent knows your priorities and biases, but other participants never see them
- **CLI-first** — Uses CLI agents (Claude, Gemini, Codex, OpenClaw) with their built-in memory as the primary backend
- **LLM-agnostic** — Each agent can use a different model / CLI tool
- **Pre-meeting briefing** — 1-on-1 prep with your agent before the meeting starts
- **Goal-driven artifacts** — Brainstorm ideas, write code, draft documents, or make decisions
- **Structured termination** — Task completion (unanimous) or 2/3 supermajority vote
- **Dual output** — Shared meeting minutes + private report for each participant
- **MCP integration** — Agents can run meetings via MCP tool calls, no CLI wrapping needed

### How It Works

1. **Configure** — Define the meeting topic, shared context, and each agent's private persona/instruction
2. **Brief** *(optional)* — Each agent has a 1-on-1 prep conversation with its owner
3. **Seal** — Start the meeting; no human input from this point
4. **Discuss** — Agents take turns speaking in shuffled rounds; every agent must participate
5. **Conclude** — Meeting ends when the goal is met or vote threshold is reached
6. **Report** — Shared minutes + goal-specific artifact + per-agent private report

## Architecture

Conclave uses a **privacy-first model**. Personas and instructions never leave the owner's machine — only utterances travel through the shared channel.

```
Alice's PC ── agent.speak() ──→ "utterance only" ──┐
                                                     │
Bob's PC  ── agent.speak() ──→ "utterance only" ──┤──→ Meeting Room
                                                     │    (server)
Carol's PC ── agent.speak() ──→ "utterance only" ──┘
```

### Roadmap

| Phase | Mode | Privacy | Status |
|-------|------|---------|--------|
| **v0.1** | Single machine | Local | ✅ Done |
| **v0.1.x** | + Briefing, artifacts, context files, CLI | Local | ✅ Done |
| **v0.2** | Central meeting room server + MCP | Platform trust | ✅ Done |
| **v1.0** | Fully distributed | Structural guarantee | Planned |

## Installation

```bash
pip install -e ".[dev]"

# With MCP server support
pip install -e ".[mcp,dev]"
```

## Quick Start

### Option 1: CLI (v0.1 — single machine)

```bash
conclave run meeting.yaml
conclave run meeting.yaml --brief    # with pre-meeting briefing
```

```yaml
# meeting.yaml
meeting_id: "brainstorm-001"
topic: "What should we build next?"
context: |
  We have 3 engineers and 2 months. Focus on user growth.
goal: brainstorm
termination: supermajority_vote
max_rounds: 5
agents:
  - agent_id: "visionary"
    owner_id: "alice"
    backend: cli
    command: claude
    instruction: "Push for ambitious, novel ideas."
  - agent_id: "pragmatist"
    owner_id: "bob"
    backend: cli
    command: claude
    instruction: "Focus on feasibility and time-to-market."
  - agent_id: "critic"
    owner_id: "carol"
    backend: cli
    command: claude
    instruction: "Find weaknesses in every idea."
```

### Option 2: Server + Clients (v0.2 — multi-machine)

```bash
# Terminal 1: Host the meeting room
conclave serve meeting-server.yaml --port 8080

# Terminal 2: Alice joins
conclave join http://server:8080 alice-agent.yaml

# Terminal 3: Bob joins
conclave join http://server:8080 bob-agent.yaml --brief
```

```yaml
# meeting-server.yaml (no agents — they join remotely)
meeting_id: "roadmap-q2"
topic: "Q2 Feature Prioritization"
context: |
  Budget allows 2 of 3 features: real-time collab, mobile v2, enterprise SSO.
goal: decision
expected_agents: 2
max_rounds: 10
```

```yaml
# alice-agent.yaml
agent_id: "eng-lead"
owner_id: "alice"
backend: cli
command: claude
instruction: "Push for SSO — it unblocks enterprise sales."
```

### Option 3: MCP (agent-native)

Configure as an MCP server:

```json
{
  "mcpServers": {
    "conclave": {
      "command": "conclave",
      "args": ["mcp"]
    }
  }
}
```

Then any MCP-capable agent can run meetings directly:

```
conclave_run(
  topic="Should we use microservices or a monolith?",
  perspectives='[
    {"name": "architect", "instruction": "Focus on long-term scalability"},
    {"name": "developer", "instruction": "Focus on development speed and simplicity"},
    {"name": "ops", "instruction": "Focus on deployment and operational cost"}
  ]',
  context="Team of 5, launching in 3 months, expecting 10k users at launch.",
  goal="decision"
)
```

MCP tools:
| Tool | Description |
|------|-------------|
| `conclave_run` | Run a complete meeting locally with multiple perspectives |
| `conclave_host` | Start a meeting room server for remote agents |
| `conclave_join` | Join a remote meeting and participate until it ends |

### Option 4: Python API

```python
import asyncio
from conclave import MeetingOrchestrator, MeetingConfig, AgentConfig, TerminationMode, MeetingGoal

config = MeetingConfig(
    meeting_id="brainstorm-001",
    topic="What should we build next?",
    context="We have 3 engineers and 2 months.",
    goal=MeetingGoal.BRAINSTORM,
    termination=TerminationMode.SUPERMAJORITY_VOTE,
    max_rounds=5,
    agents=[
        AgentConfig(
            agent_id="visionary",
            owner_id="alice",
            backend="cli",
            command="claude",
            instruction="Push for ambitious ideas.",
        ),
        AgentConfig(
            agent_id="pragmatist",
            owner_id="bob",
            backend="cli",
            command="claude",
            instruction="Focus on feasibility.",
        ),
    ],
)

result = asyncio.run(MeetingOrchestrator(config).run())

print(result.minutes.summary)        # Shared minutes
print(result.artifact.content)        # Goal-specific deliverable
print(result.personal_reports["alice"].summary)  # Private report
```

## Meeting Goals

| Goal | Output | Use Case |
|------|--------|----------|
| `brainstorm` | Ranked ideas + vote results | Ideation, exploration |
| `code` | Working code / patches | Technical design, implementation |
| `document` | Polished document | Proposals, specs, reports |
| `decision` | Decision record with rationale | Architecture choices, prioritization |

All goals also produce shared minutes + per-agent personal reports.

## License

MIT
