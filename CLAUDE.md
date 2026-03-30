# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Conclave is a multi-agent meeting system where AI agents represent humans in meetings. Inspired by Andrej Karpathy's AutoResearch and the papal conclave concept — agents enter, the door seals, and no human intervenes until the meeting concludes.

## Architecture

### Core Concepts
- **Agent**: LLM-backed representative with a private persona (never shared with other agents) and shared meeting context
- **Orchestrator**: Runs the sealed meeting loop — round-based free discussion with shuffled turn order
- **MeetingRoom**: v0.2 server that hosts meetings for remote agents (HTTP-based coordination)
- **MeetingClient**: v0.2 client that connects a local agent to a remote meeting room
- **Privacy boundary**: Structural, not policy-based. Persona exists only inside Agent's system prompt; transcript contains only utterances
- **Termination**: Task completion (unanimous) or supermajority vote (2/3), with hard limits on rounds/tokens

### Module Map
- `models.py` — Pydantic data models (shared vocabulary for all modules)
- `agent.py` — Agent class: persona injection, speak, vote
- `llm.py` — Thin litellm wrapper with retry/token counting
- `orchestrator.py` — v0.1 meeting main loop: rounds → turns → votes → termination
- `server.py` — v0.2 meeting room HTTP server (aiohttp): hosts meetings, coordinates turns
- `client.py` — v0.2 remote agent client: joins server, runs agent locally
- `turn.py` — Turn strategy (round-robin with shuffle)
- `vote.py` — Voting mechanism and termination detection
- `output.py` — Shared minutes + per-agent personal report generation
- `briefing.py` — Pre-meeting 1-on-1 briefing between user and agent
- `config.py` — YAML/JSON config loading and validation
- `cli.py` — CLI entrypoint: `run`, `serve`, `join` subcommands

### Deployment Model
Conclave uses a **distributed architecture** where each participant runs their own agent locally. Personas never leave the owner's machine — only utterances are shared through a coordination channel.

```
Alice's PC ── agent.speak() ──→ "utterance only" ──┐
                                                     │
Bob's PC  ── agent.speak() ──→ "utterance only" ──┤──→ Shared Channel
                                                     │    (orchestrator)
Carol's PC ── agent.speak() ──→ "utterance only" ──┘
```

Phases — goal: first-class tool for AI agents (OpenClaw, Claude, Gemini, Codex):
- **v0.1** — Single machine: all agents run locally ✅
- **v0.1.x** — Briefing, artifacts, context files, CLI entrypoint ✅
- **v0.2** — Central server + remote clients + MCP tools ✅
- **v0.4** — Stable agent platform: multi-meeting, auth, persistence, meeting discovery ✅
- **v0.6** — Smart agent workflows: templates, chaining, progress streaming, webhooks, reconnection ✅
- **v0.8** — Autonomous ecosystem: agent invitations, scheduling, E2E encryption, federation
- **v1.0** — Fully distributed: P2P, structural privacy guarantee

### v0.2 Architecture
```
Alice's PC                          Meeting Server                      Bob's PC
┌─────────────┐                    ┌──────────────────┐               ┌─────────────┐
│ conclave join│── utterance ──→   │ POST /respond    │  ←── utterance│conclave join│
│ (claude CLI) │←── action ──────  │ GET  /next       │  ── action ──→│ (gemini CLI)│
│ persona: 🔒  │                   │ MeetingRoom      │               │ persona: 🔒  │
└─────────────┘                    │ (turns/votes/end)│               └─────────────┘
                                    └──────────────────┘
                                     conclave serve
```

Server API:
- `GET  /meeting` — meeting info
- `POST /meeting/join` — register agent
- `POST /meeting/seal` — manually seal (or auto-seal via expected_agents)
- `GET  /meeting/next?agent_id=X` — long-poll for next action
- `POST /meeting/respond` — submit utterance/vote/generated content
- `GET  /meeting/result` — final results after meeting completes

### Data Flow
```
v0.1: MeetingConfig → Orchestrator.run() → [Round loop: Turn → Speak → Vote] → OutputGenerator → MeetingResult
v0.2: MeetingConfig → Server(MeetingRoom) ←HTTP→ Client(Agent) → [Round loop via HTTP] → Results
                                      ↑ sealed: no human input ↑
```

### Privacy Model
| Data | v0.1 Scope | v0.2 Scope |
|------|-----------|-----------|
| `AgentConfig.persona/instruction` | Agent's own LLM calls only | Client-side only (never sent to server) |
| `MeetingConfig.topic/context` | All agents (shared) | Server + all clients (shared) |
| `Message.content` (utterances) | All agents (shared transcript) | Server + all clients (shared) |
| `PersonalReport` | Owner only | Generated on client, sent to server (platform trust) |

## Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest
pytest tests/test_server.py           # server/client tests
pytest tests/test_server.py::test_name # single test

# v0.1 — Run locally
conclave run meeting.yaml [--brief] [-v]

# v0.2 — Host a meeting room
conclave serve meeting.yaml [--host 0.0.0.0] [--port 8080] [-v]

# v0.2 — Join a remote meeting
conclave join http://server:8080 agent.yaml [--brief] [-v]
```

## Tech Stack
- Python 3.11+
- `aiohttp` for v0.2 HTTP server/client
- `litellm` for LLM-agnostic API calls
- `pydantic` v2 for data models/validation
- `pyyaml` for config files
- `asyncio` throughout (meetings are I/O-bound)

## Conventions
- All LLM calls go through `llm.py` — never call litellm directly from other modules
- Config is always validated through Pydantic before any LLM call
- Agents must never access another agent's persona — this is enforced structurally
- v0.2 server never stores or forwards persona/instruction data
