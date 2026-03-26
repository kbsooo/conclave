"""Shared data models — the vocabulary every module imports from."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class TerminationMode(str, Enum):
    """How the meeting decides to end."""
    TASK_COMPLETION = "task_completion"        # unanimous: all agents agree task is done
    SUPERMAJORITY_VOTE = "supermajority_vote"  # 2/3 of agents vote to conclude

class MeetingGoal(str, Enum):
    """What the meeting aims to produce."""
    BRAINSTORM = "brainstorm"          # generate and rank ideas
    CODE = "code"                      # produce code / patches
    DOCUMENT = "document"              # draft a document (proposal, spec, report)
    DECISION = "decision"              # reach and record a decision with rationale

class MeetingStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# ── Agent Config ───────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    """Per-agent configuration. Private context never leaves the owner's machine."""
    agent_id: str
    owner_id: str                              # one owner can have multiple agents

    # Backend selection: "cli" (primary) or "api" (fallback)
    backend: str = "cli"

    # CLI backend fields — the agent already knows the user via its memory
    command: str = "claude"                    # CLI agent command (claude, openclaw, codex, etc.)
    instruction: str = ""                      # light guidance for this meeting (the agent's memory does the rest)
    cli_args: list[str] | None = None          # override default CLI arguments
    cli_timeout: int = 300                     # seconds before killing the CLI process

    # API backend fields — no memory, so needs full persona
    persona: str = ""                          # required for API backend; secret, only this agent's LLM sees it
    model: str = "openai/gpt-4o-mini"          # litellm model string
    temperature: float = 0.7


# ── Meeting Config ─────────────────────────────────────────────────────

class MeetingConfig(BaseModel):
    """Full meeting specification. Validated before any LLM call."""
    meeting_id: str
    topic: str                                 # shared with all agents
    context: str = ""                          # additional shared background
    context_files: list[str] = Field(default_factory=list)  # files loaded into shared context
    goal: MeetingGoal = MeetingGoal.BRAINSTORM # what the meeting aims to produce
    agents: list[AgentConfig] = Field(default_factory=list)
    expected_agents: int = 0                   # v0.2+: auto-seal after N agents join (0 = manual seal)
    termination: TerminationMode = TerminationMode.SUPERMAJORITY_VOTE
    max_rounds: int = 20                       # hard cost ceiling
    max_tokens_per_agent: int = 4096           # per-call token limit
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Server Config (v0.4) ─────────────────────────────────────────────

class ServerConfig(BaseModel):
    """Server-level configuration (not per-meeting)."""
    api_keys: list[str] = Field(default_factory=list)  # empty = auto-generate one key
    data_dir: str = "~/.conclave"              # persistence directory
    max_meetings: int = 50                     # concurrent meeting limit


# ── Transcript ─────────────────────────────────────────────────────────

class Message(BaseModel):
    """Single utterance in the shared transcript. Never contains persona."""
    role: Literal["agent", "system"]
    agent_id: str | None = None                # None for system messages
    content: str
    round_number: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Meeting State ──────────────────────────────────────────────────────

class MeetingState(BaseModel):
    """Mutable meeting state, managed by the orchestrator."""
    config: MeetingConfig
    transcript: list[Message] = Field(default_factory=list)
    current_round: int = 0
    votes: dict[str, bool] = Field(default_factory=dict)   # agent_id → wants to end
    status: MeetingStatus = MeetingStatus.PENDING
    termination_reason: str | None = None


# ── Outputs ────────────────────────────────────────────────────────────

class Minutes(BaseModel):
    """Shared meeting minutes — identical for all participants."""
    summary: str
    key_points: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)

class PersonalReport(BaseModel):
    """Per-agent report — only visible to the agent's owner."""
    owner_id: str
    agent_id: str
    summary: str
    recommendations: list[str] = Field(default_factory=list)

class Artifact(BaseModel):
    """Concrete deliverable produced by the meeting (code, document, etc.)."""
    goal: MeetingGoal
    content: str                               # the artifact text (code, document, decision record)
    title: str = ""

class MeetingResult(BaseModel):
    """Final output of a completed meeting."""
    meeting_id: str
    status: MeetingStatus
    termination_reason: str
    transcript: list[Message] = Field(default_factory=list)
    minutes: Minutes
    artifact: Artifact | None = None           # goal-specific deliverable
    personal_reports: dict[str, PersonalReport] = Field(default_factory=dict)  # owner_id → report
