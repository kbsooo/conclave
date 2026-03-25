"""Conclave: Multi-agent meeting system where AI agents represent humans."""

from conclave.briefing import brief_all_agents
from conclave.config import load_meeting_config
from conclave.models import (
    AgentConfig,
    MeetingConfig,
    MeetingGoal,
    MeetingResult,
    MeetingStatus,
    TerminationMode,
)
from conclave.orchestrator import MeetingOrchestrator

__all__ = [
    "AgentConfig",
    "MeetingConfig",
    "MeetingGoal",
    "MeetingOrchestrator",
    "MeetingResult",
    "MeetingStatus",
    "TerminationMode",
    "brief_all_agents",
    "load_meeting_config",
]
