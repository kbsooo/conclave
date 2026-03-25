"""Output generation — shared minutes, artifacts, and per-agent personal reports.

Uses the same Backend abstraction as agents, so it works with both
CLI agents (claude, openclaw) and API calls (litellm).
"""

from __future__ import annotations

import json
import logging

from conclave.agent import Agent
from conclave.backend import Backend
from conclave.models import Artifact, MeetingGoal, MeetingState, Minutes, PersonalReport

logger = logging.getLogger(__name__)

MINUTES_PROMPT = """\
You are a meeting minutes writer. Summarize the following meeting transcript.
Respond in JSON with these fields:
- "summary": string (2-3 sentence overview)
- "key_points": list of strings
- "decisions": list of strings (decisions made, if any)
- "action_items": list of strings (next steps, if any)
Be concise and neutral. Do not add information not in the transcript.
Output ONLY valid JSON, no markdown fences."""

# Goal-specific artifact extraction prompts
ARTIFACT_PROMPTS: dict[MeetingGoal, str] = {
    MeetingGoal.BRAINSTORM: """\
Extract the final ideas from this brainstorming meeting.
List each idea with a brief description and note which ones had the most support.
If the group converged on a winner, highlight it clearly.""",

    MeetingGoal.CODE: """\
Extract the final code artifact from this meeting discussion.
Consolidate all code discussed into a single, coherent, working result.
Include only the final agreed-upon version, not intermediate drafts.
Output the code directly, ready to use.""",

    MeetingGoal.DOCUMENT: """\
Consolidate the meeting discussion into a final document.
Produce a polished, structured document based on what the participants agreed upon.
Use appropriate headings, sections, and formatting.
This should read as a finished deliverable, not a summary of the discussion.""",

    MeetingGoal.DECISION: """\
Extract the decision(s) made in this meeting.
For each decision, include:
- The decision itself
- Key arguments for and against that were discussed
- The rationale for the final choice
- Any conditions, caveats, or follow-up actions
Format as a clear decision record.""",
}


class OutputGenerator:
    """Generates meeting outputs using any Backend."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def generate_minutes(self, state: MeetingState) -> Minutes:
        """Summarize transcript into shared meeting minutes.

        Uses a neutral prompt with no persona — same output for everyone.
        """
        transcript_text = self._format_transcript(state)

        prompt = (
            f"{MINUTES_PROMPT}\n\n"
            f"Meeting topic: {state.config.topic}\n\n"
            f"Transcript:\n{transcript_text}"
        )

        response = await self._backend.generate(prompt)

        # Try to parse JSON from the response (strip markdown fences if present)
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(cleaned)
            return Minutes.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse minutes JSON, using raw content: %s", e)
            return Minutes(summary=response)

    async def generate_artifact(self, state: MeetingState) -> Artifact | None:
        """Generate a goal-specific artifact from the meeting transcript.

        Different meeting goals produce different deliverables:
        - BRAINSTORM → ranked ideas
        - CODE → consolidated code
        - DOCUMENT → polished document
        - DECISION → decision record with rationale
        """
        goal = state.config.goal
        artifact_prompt = ARTIFACT_PROMPTS.get(goal)
        if artifact_prompt is None:
            return None

        transcript_text = self._format_transcript(state)

        prompt = (
            f"{artifact_prompt}\n\n"
            f"Meeting topic: {state.config.topic}\n"
            f"Meeting context: {state.config.context}\n\n"
            f"Transcript:\n{transcript_text}"
        )

        content = await self._backend.generate(prompt)

        return Artifact(
            goal=goal,
            content=content,
            title=state.config.topic,
        )

    async def generate_personal_report(
        self, state: MeetingState, agent: Agent,
    ) -> PersonalReport:
        """Generate report from the agent's perspective for its owner."""
        report_text = await agent.write_personal_report(state.transcript)

        return PersonalReport(
            owner_id=agent.owner_id,
            agent_id=agent.agent_id,
            summary=report_text,
        )

    def _format_transcript(self, state: MeetingState) -> str:
        """Format transcript — utterances only, no persona info."""
        lines: list[str] = []
        for msg in state.transcript:
            if msg.role == "system":
                lines.append(f"[System] {msg.content}")
            else:
                lines.append(f"[{msg.agent_id}] {msg.content}")
        return "\n\n".join(lines)
