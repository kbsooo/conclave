"""Remote agent client — joins a meeting room server (v0.2).

Runs a local agent (CLI or API backend) and connects to the meeting server.
The agent's persona/instruction stays local; only utterances are sent.

Usage:
    client = MeetingClient("http://server:8080", agent_config)
    result = await client.run()
"""

from __future__ import annotations

import logging

import aiohttp

from conclave.agent import Agent
from conclave.backend import create_backend
from conclave.models import AgentConfig, MeetingGoal, Message

logger = logging.getLogger(__name__)


class MeetingClient:
    """Connects a local agent to a remote meeting room."""

    def __init__(self, server_url: str, agent_config: AgentConfig) -> None:
        self.server_url = server_url.rstrip("/")
        self.agent_config = agent_config
        self.agent_id = agent_config.agent_id
        self._backend = create_backend(
            backend_type=agent_config.backend,
            command=agent_config.command,
            model=agent_config.model,
            temperature=agent_config.temperature,
            cli_args=agent_config.cli_args,
            cli_timeout=agent_config.cli_timeout,
        )
        self._agent: Agent | None = None

    async def run(self) -> dict | None:
        """Join the meeting and participate until it ends.

        Returns the meeting result dict from the server.
        """
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Join
            meeting_info = await self._join(session)
            logger.info("Joined meeting: %s", meeting_info["meeting_id"])

            print(f"\nJoined meeting: {meeting_info['topic']}")
            print(f"  Goal: {meeting_info['goal']}")
            print(
                f"  Agents: {meeting_info.get('agents_joined', len(meeting_info.get('agents', [])))}/"
                f"{meeting_info['expected_agents']}"
            )
            print("  Waiting for meeting to start...\n")

            # 2. Create local agent with meeting context
            self._agent = Agent(
                config=self.agent_config,
                meeting_topic=meeting_info["topic"],
                meeting_context=meeting_info.get("context", ""),
                meeting_goal=MeetingGoal(meeting_info["goal"]),
                backend=self._backend,
            )

            # 3. Action loop
            result = None
            try:
                while True:
                    action = await self._get_next(session)
                    action_type = action.get("action")

                    if action_type == "wait":
                        continue
                    elif action_type == "done":
                        result = action.get("result")
                        break
                    elif action_type == "speak":
                        await self._handle_speak(session, action)
                    elif action_type == "vote":
                        await self._handle_vote(session, action)
                    elif action_type == "generate":
                        await self._handle_generate(session, action)
                    elif action_type == "generate_report":
                        await self._handle_generate_report(session, action)
                    else:
                        logger.warning("Unknown action: %s", action_type)
            except (aiohttp.ClientError, OSError) as e:
                # Server shut down — normal at meeting end
                if result is None:
                    logger.warning("Lost connection to server: %s", e)
                    print(f"\n  Connection lost: {e}")

            return result

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _join(self, session: aiohttp.ClientSession) -> dict:
        async with session.post(
            f"{self.server_url}/meeting/join",
            json={
                "agent_id": self.agent_id,
                "owner_id": self.agent_config.owner_id,
            },
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Join failed: {data.get('error')}")
            return data

    async def _get_next(self, session: aiohttp.ClientSession) -> dict:
        async with session.get(
            f"{self.server_url}/meeting/next",
            params={"agent_id": self.agent_id},
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            return await resp.json()

    async def _respond(self, session: aiohttp.ClientSession, content: str) -> None:
        async with session.post(
            f"{self.server_url}/meeting/respond",
            json={"agent_id": self.agent_id, "content": content},
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Respond failed: {data.get('error')}")

    # ── Action handlers ─────────────────────────────────────────────

    def _parse_transcript(self, raw: list[dict]) -> list[Message]:
        return [
            Message(
                role=m["role"],
                agent_id=m.get("agent_id"),
                content=m["content"],
                round_number=m["round_number"],
            )
            for m in raw
        ]

    async def _handle_speak(
        self, session: aiohttp.ClientSession, action: dict,
    ) -> None:
        """Generate utterance using local agent (with persona/memory)."""
        transcript = self._parse_transcript(action["transcript"])
        round_number = action["round_number"]

        logger.info("Speaking in round %d...", round_number)
        print(f"[Round {round_number}] Speaking...")

        utterance = await self._agent.speak(transcript, round_number)
        print(f"  [{self.agent_id}] {utterance[:200]}{'...' if len(utterance) > 200 else ''}")

        await self._respond(session, utterance)

    async def _handle_vote(
        self, session: aiohttp.ClientSession, action: dict,
    ) -> None:
        """Vote using local agent."""
        transcript = self._parse_transcript(action["transcript"])
        vote = await self._agent.vote_to_end(transcript)
        logger.info("Vote: %s", "YES" if vote else "NO")

        await self._respond(session, "YES" if vote else "NO")

    async def _handle_generate(
        self, session: aiohttp.ClientSession, action: dict,
    ) -> None:
        """Generate shared output (minutes/artifact) — server provides the prompt."""
        output_type = action.get("output_type", "unknown")
        prompt = action["prompt"]

        logger.info("Generating %s...", output_type)
        print(f"  Generating {output_type}...")

        content = await self._backend.generate(prompt)
        await self._respond(session, content)

    async def _handle_generate_report(
        self, session: aiohttp.ClientSession, action: dict,
    ) -> None:
        """Generate personal report — uses Agent (includes persona/instruction)."""
        transcript = self._parse_transcript(action["transcript"])

        logger.info("Generating personal report...")
        print("  Generating personal report...")

        report = await self._agent.write_personal_report(transcript)
        await self._respond(session, report)
