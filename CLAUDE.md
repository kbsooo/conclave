# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Conclave is a multi-agent meeting system where AI agents represent humans in meetings. Inspired by Andrej Karpathy's AutoResearch and the papal conclave concept — agents enter, the door seals, and no human intervenes until the meeting concludes.

## Architecture

### Core Concepts
- **Agent**: LLM-backed representative with a private persona (never shared with other agents) and shared meeting context
- **Orchestrator**: Runs the sealed meeting loop — round-based free discussion with shuffled turn order
- **Privacy boundary**: Structural, not policy-based. Persona exists only inside Agent's system prompt; transcript contains only utterances
- **Termination**: Task completion (unanimous) or supermajority vote (2/3), with hard limits on rounds/tokens

### Module Map
- `models.py` — Pydantic data models (shared vocabulary for all modules)
- `agent.py` — Agent class: persona injection, speak, vote
- `llm.py` — Thin litellm wrapper with retry/token counting
- `orchestrator.py` — Meeting main loop: rounds → turns → votes → termination
- `turn.py` — Turn strategy (round-robin with shuffle)
- `vote.py` — Voting mechanism and termination detection
- `output.py` — Shared minutes + per-agent personal report generation
- `config.py` — YAML/JSON config loading and validation

### Deployment Model
Conclave uses a **distributed architecture** where each participant runs their own agent locally. Personas never leave the owner's machine — only utterances are shared through a coordination channel.

```
Alice's PC ── agent.speak() ──→ "utterance only" ──┐
                                                     │
Bob's PC  ── agent.speak() ──→ "utterance only" ──┤──→ Shared Channel
                                                     │    (orchestrator)
Carol's PC ── agent.speak() ──→ "utterance only" ──┘
```

This is planned in phases:
- **v0.1** — Single machine: all agents run locally (solo brainstorming, prototyping) ✅
- **v0.1.x** — Pre-meeting briefing (1-on-1 prep with your agent) + artifact generation (code, documents, not just minutes) ✅
- **v0.2** — Shared platform as meeting room: agents join an existing collaboration platform (moltbook-style) to discuss, no custom server needed
- **v1.0** — Distributed: each participant runs their agent locally, shared channel carries utterances only (full privacy)

### Pre-meeting Briefing (planned)
Before the conclave is sealed, each participant has a 1-on-1 conversation with their agent:
1. User tells the agent what they want from this meeting
2. Agent combines instructions with its built-in memory of the user
3. Agent may ask clarifying questions
4. Result: a prepared agent with richer context than a static `instruction` string

### Artifact Generation (planned)
Meetings produce different outputs depending on the goal:
- **Brainstorming** → ranked ideas + vote results (currently implemented)
- **Code work** → code files / patches
- **Document drafting** → completed document (proposal, spec, report)
- **Decision making** → decision record with rationale
All types still produce shared minutes + per-agent personal reports.

### Data Flow
```
MeetingConfig → Orchestrator.run() → [Round loop: Turn → Speak → Vote] → OutputGenerator → MeetingResult
                                      ↑ sealed: no human input ↑
```

### Privacy Model
| Data | Scope |
|------|-------|
| `AgentConfig.persona` | Agent's own LLM calls only |
| `MeetingConfig.topic/context` | All agents (shared) |
| `Message.content` | All agents (shared transcript) |
| `PersonalReport` | Owner only |

## Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest
pytest tests/test_orchestrator.py       # single test file
pytest tests/test_agent.py::test_name   # single test

# Run example
python examples/brainstorm.py
```

## Tech Stack
- Python 3.11+
- `litellm` for LLM-agnostic API calls
- `pydantic` v2 for data models/validation
- `pyyaml` for config files
- `asyncio` throughout (meetings are I/O-bound)

## Conventions
- All LLM calls go through `llm.py` — never call litellm directly from other modules
- Config is always validated through Pydantic before any LLM call
- Agents must never access another agent's persona — this is enforced structurally
