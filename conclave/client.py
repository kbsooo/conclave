"""Remote agent client — joins a meeting room server (v0.4).

Runs a local agent (CLI or API backend) and connects to the meeting server.
The agent's persona/instruction stays local; only utterances are sent.

Usage:
    client = MeetingClient("http://server:8080", agent_config, meeting_id="brainstorm-1", api_key="...")
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

    def __init__(
        self,
        server_url: str,
        agent_config: AgentConfig,
        meeting_id: str = "",
        api_key: str = "",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.agent_config = agent_config
        self.agent_id = agent_config.agent_id
        self.meeting_id = meeting_id
        self._api_key = api_key
        self._backend = create_backend(
            backend_type=agent_config.backend,
            command=agent_config.command,
            model=agent_config.model,
            temperature=agent_config.temperature,
            cli_args=agent_config.cli_args,
            cli_timeout=agent_config.cli_timeout,
        )
        self._agent: Agent | None = None

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _meeting_url(self, path: str) -> str:
        return f"{self.server_url}/meetings/{self.meeting_id}{path}"

    MAX_RECONNECTS = 5
    RECONNECT_DELAY = 2.0  # seconds, doubles each retry

    async def run(self) -> dict | None:
        """Join the meeting and participate until it ends.

        Automatically reconnects on disconnection (up to MAX_RECONNECTS times).
        """
        timeout = aiohttp.ClientTimeout(total=300)
        headers = self._headers()
        result = None
        reconnects = 0

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            # Auto-discover meeting if no meeting_id specified
            if not self.meeting_id:
                self.meeting_id = await self._discover_meeting(session)

            # 1. Join
            meeting_info = await self._join(session)
            reconnected = meeting_info.get("reconnected", False)

            if reconnected:
                print(f"\n  Reconnected to meeting (round {meeting_info.get('current_round', '?')})")
            else:
                logger.info("Joined meeting: %s", meeting_info["meeting_id"])
                print(f"\nJoined meeting: {meeting_info['topic']}")
                print(f"  Goal: {meeting_info['goal']}")
                print(
                    f"  Agents: {len(meeting_info.get('agents', []))}/"
                    f"{meeting_info['expected_agents']}"
                )
                print("  Waiting for meeting to start...\n")

            # 2. Create local agent (once)
            if self._agent is None:
                self._agent = Agent(
                    config=self.agent_config,
                    meeting_topic=meeting_info["topic"],
                    meeting_context=meeting_info.get("context", ""),
                    meeting_goal=MeetingGoal(meeting_info["goal"]),
                    backend=self._backend,
                )

            # 3. Action loop with reconnection
            while True:
                try:
                    action = await self._get_next(session)
                    reconnects = 0  # reset on success
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
                    reconnects += 1
                    if reconnects > self.MAX_RECONNECTS:
                        logger.error("Max reconnection attempts reached")
                        print(f"\n  Connection lost permanently: {e}")
                        break

                    delay = self.RECONNECT_DELAY * (2 ** (reconnects - 1))
                    logger.warning("Connection lost, reconnecting in %.0fs (%d/%d)...",
                                   delay, reconnects, self.MAX_RECONNECTS)
                    print(f"  Reconnecting in {delay:.0f}s...")
                    await asyncio.sleep(delay)

                    try:
                        info = await self._join(session)
                        if info.get("reconnected"):
                            print(f"  Reconnected (round {info.get('current_round', '?')})")
                    except Exception as re_err:
                        logger.warning("Reconnect attempt failed: %s", re_err)

            return result

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _discover_meeting(self, session: aiohttp.ClientSession) -> str:
        """Find the first pending meeting on the server."""
        async with session.get(
            f"{self.server_url}/meetings",
            params={"status": "pending"},
        ) as resp:
            meetings = await resp.json()
            if not meetings:
                raise RuntimeError("No pending meetings found on server")
            return meetings[0]["meeting_id"]

    async def _join(self, session: aiohttp.ClientSession) -> dict:
        async with session.post(
            self._meeting_url("/join"),
            json={"agent_id": self.agent_id, "owner_id": self.agent_config.owner_id},
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Join failed: {data.get('error')}")
            return data

    async def _get_next(self, session: aiohttp.ClientSession) -> dict:
        async with session.get(
            self._meeting_url("/next"),
            params={"agent_id": self.agent_id},
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            return await resp.json()

    async def _respond(self, session: aiohttp.ClientSession, content: str) -> None:
        async with session.post(
            self._meeting_url("/respond"),
            json={"agent_id": self.agent_id, "content": content},
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"Respond failed: {data.get('error')}")

    # ── Action handlers ─────────────────────────────────────────────

    def _parse_transcript(self, raw: list[dict]) -> list[Message]:
        return [
            Message(
                role=m["role"], agent_id=m.get("agent_id"),
                content=m["content"], round_number=m["round_number"],
            )
            for m in raw
        ]

    async def _handle_speak(self, session: aiohttp.ClientSession, action: dict) -> None:
        transcript = self._parse_transcript(action["transcript"])
        round_number = action["round_number"]
        logger.info("Speaking in round %d...", round_number)
        print(f"[Round {round_number}] Speaking...")
        utterance = await self._agent.speak(transcript, round_number)
        print(f"  [{self.agent_id}] {utterance[:200]}{'...' if len(utterance) > 200 else ''}")
        await self._respond(session, utterance)

    async def _handle_vote(self, session: aiohttp.ClientSession, action: dict) -> None:
        transcript = self._parse_transcript(action["transcript"])
        vote = await self._agent.vote_to_end(transcript)
        logger.info("Vote: %s", "YES" if vote else "NO")
        await self._respond(session, "YES" if vote else "NO")

    async def _handle_generate(self, session: aiohttp.ClientSession, action: dict) -> None:
        output_type = action.get("output_type", "unknown")
        prompt = action["prompt"]
        logger.info("Generating %s...", output_type)
        print(f"  Generating {output_type}...")
        content = await self._backend.generate(prompt)
        await self._respond(session, content)

    async def _handle_generate_report(self, session: aiohttp.ClientSession, action: dict) -> None:
        transcript = self._parse_transcript(action["transcript"])
        logger.info("Generating personal report...")
        print("  Generating personal report...")
        report = await self._agent.write_personal_report(transcript)
        await self._respond(session, report)
