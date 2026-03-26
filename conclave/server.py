"""Meeting room server — the shared conclave space (v0.2).

Hosts a meeting that remote agents can join. The server handles:
- Agent registration and sealing
- Turn coordination (shuffled round-robin)
- Vote collection and termination detection
- Transcript management and output collection

Privacy: only utterances pass through the server. Personas stay on clients.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random

from aiohttp import web

from conclave.models import (
    MeetingConfig,
    MeetingStatus,
    Message,
)
from conclave.output import ARTIFACT_PROMPTS, MINUTES_PROMPT

logger = logging.getLogger(__name__)


# ── Meeting Room ────────────────────────────────────────────────────


class AgentSlot:
    """A registered agent in the meeting room."""

    def __init__(self, agent_id: str, owner_id: str) -> None:
        self.agent_id = agent_id
        self.owner_id = owner_id
        self.action_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.response_future: asyncio.Future[str] | None = None


class MeetingRoom:
    """Manages a single meeting's lifecycle on the server side.

    Flow: agents join → seal → rounds (speak → vote) → outputs → done
    """

    def __init__(self, config: MeetingConfig) -> None:
        self.config = config
        self.agents: dict[str, AgentSlot] = {}
        self.transcript: list[Message] = []
        self.current_round: int = 0
        self.status: MeetingStatus = MeetingStatus.PENDING
        self.termination_reason: str | None = None
        self.result_data: dict | None = None

        self._sealed = asyncio.Event()
        self._meeting_task: asyncio.Task | None = None

    # ── Registration ────────────────────────────────────────────────

    def join(self, agent_id: str, owner_id: str) -> dict:
        """Register an agent. Auto-seals when expected_agents reached."""
        if self._sealed.is_set():
            raise ValueError("Meeting already sealed, cannot join")
        if agent_id in self.agents:
            raise ValueError(f"Agent '{agent_id}' already registered")

        self.agents[agent_id] = AgentSlot(agent_id, owner_id)
        logger.info(
            "Agent '%s' (owner: %s) joined — %d/%d",
            agent_id, owner_id, len(self.agents), self.config.expected_agents,
        )

        if (
            self.config.expected_agents > 0
            and len(self.agents) >= self.config.expected_agents
        ):
            self.seal()

        return self._meeting_info()

    def seal(self) -> None:
        """Seal the meeting and start the conclave."""
        if self._sealed.is_set():
            return
        if not self.agents:
            raise ValueError("No agents registered")

        self._sealed.set()
        self._meeting_task = asyncio.create_task(self._run_meeting())
        logger.info("Meeting sealed with %d agents", len(self.agents))

    # ── Client interaction ──────────────────────────────────────────

    async def get_next_action(self, agent_id: str) -> dict:
        """Block until an action is available for this agent."""
        if agent_id not in self.agents:
            raise ValueError(f"Unknown agent: {agent_id}")
        return await self.agents[agent_id].action_queue.get()

    def submit_response(self, agent_id: str, content: str) -> None:
        """Client submits a response to its current action."""
        if agent_id not in self.agents:
            raise ValueError(f"Unknown agent: {agent_id}")
        slot = self.agents[agent_id]
        if slot.response_future and not slot.response_future.done():
            slot.response_future.set_result(content)
        else:
            raise ValueError(f"No pending action for agent: {agent_id}")

    # ── Meeting loop ────────────────────────────────────────────────

    async def _run_meeting(self) -> None:
        self.status = MeetingStatus.IN_PROGRESS

        self.transcript.append(Message(
            role="system",
            content=f"Meeting started. Topic: {self.config.topic}",
            round_number=0,
        ))

        try:
            while self.current_round < self.config.max_rounds:
                self.current_round += 1
                if not await self._run_round():
                    break

            if self.termination_reason is None:
                self.termination_reason = (
                    f"Max rounds ({self.config.max_rounds}) reached"
                )

            await self._collect_outputs()

        except Exception:
            logger.exception("Meeting failed")
            self.termination_reason = "Error during meeting"

        self.status = MeetingStatus.COMPLETED

        # Signal all agents: done
        for slot in self.agents.values():
            await slot.action_queue.put({
                "action": "done",
                "result": self.result_data,
            })

        logger.info("Meeting completed: %s", self.termination_reason)

    async def _run_round(self) -> bool:
        """One round: each agent speaks once, then all vote."""
        agent_ids = list(self.agents.keys())
        random.shuffle(agent_ids)

        logger.info("Round %d — order: %s", self.current_round, agent_ids)

        # ── Speaking phase ──────────────────────────────────────────
        for agent_id in agent_ids:
            slot = self.agents[agent_id]
            loop = asyncio.get_running_loop()
            slot.response_future = loop.create_future()

            await slot.action_queue.put({
                "action": "speak",
                "transcript": self._transcript_dicts(),
                "round_number": self.current_round,
            })

            utterance = await slot.response_future
            slot.response_future = None

            self.transcript.append(Message(
                role="agent",
                agent_id=agent_id,
                content=utterance,
                round_number=self.current_round,
            ))
            logger.debug("[%s] %s", agent_id, utterance[:100])

        # ── Voting phase (concurrent) ──────────────────────────────
        loop = asyncio.get_running_loop()
        futures: dict[str, asyncio.Future[str]] = {}

        for agent_id, slot in self.agents.items():
            slot.response_future = loop.create_future()
            await slot.action_queue.put({
                "action": "vote",
                "transcript": self._transcript_dicts(),
            })
            futures[agent_id] = slot.response_future

        votes: dict[str, bool] = {}
        for agent_id, future in futures.items():
            raw = await future
            self.agents[agent_id].response_future = None
            votes[agent_id] = (
                raw.strip().upper().startswith("YES")
                if isinstance(raw, str)
                else bool(raw)
            )

        yes_count = sum(1 for v in votes.values() if v)
        total = len(votes)
        logger.info("Round %d votes: %d/%d", self.current_round, yes_count, total)

        # Termination check (same logic as VoteManager)
        from conclave.models import TerminationMode
        if self.config.termination == TerminationMode.SUPERMAJORITY_VOTE:
            threshold = math.ceil(total * 2 / 3)
            terminate = yes_count >= threshold
        else:
            terminate = yes_count == total

        if terminate:
            self.termination_reason = (
                f"Vote passed ({yes_count}/{total}) after round {self.current_round}"
            )
            return False

        return True

    # ── Output collection ───────────────────────────────────────────

    async def _collect_outputs(self) -> None:
        """Ask agents to generate meeting outputs."""
        transcript_text = self._format_transcript()
        first_id = next(iter(self.agents))
        first_slot = self.agents[first_id]
        loop = asyncio.get_running_loop()

        # Shared minutes — first agent generates
        minutes_prompt = (
            f"{MINUTES_PROMPT}\n\n"
            f"Meeting topic: {self.config.topic}\n\n"
            f"Transcript:\n{transcript_text}"
        )
        first_slot.response_future = loop.create_future()
        await first_slot.action_queue.put({
            "action": "generate",
            "prompt": minutes_prompt,
            "output_type": "minutes",
        })
        minutes_raw = await first_slot.response_future
        first_slot.response_future = None

        # Goal-specific artifact — first agent generates
        artifact_raw = None
        artifact_prompt_text = ARTIFACT_PROMPTS.get(self.config.goal)
        if artifact_prompt_text:
            full_prompt = (
                f"{artifact_prompt_text}\n\n"
                f"Meeting topic: {self.config.topic}\n"
                f"Meeting context: {self.config.context}\n\n"
                f"Transcript:\n{transcript_text}"
            )
            first_slot.response_future = loop.create_future()
            await first_slot.action_queue.put({
                "action": "generate",
                "prompt": full_prompt,
                "output_type": "artifact",
            })
            artifact_raw = await first_slot.response_future
            first_slot.response_future = None

        # Personal reports — each agent generates their own (concurrent)
        report_futures: dict[str, asyncio.Future[str]] = {}
        for agent_id, slot in self.agents.items():
            slot.response_future = loop.create_future()
            await slot.action_queue.put({
                "action": "generate_report",
                "transcript": self._transcript_dicts(),
            })
            report_futures[agent_id] = slot.response_future

        reports: dict[str, str] = {}
        for agent_id, future in report_futures.items():
            reports[agent_id] = await future
            self.agents[agent_id].response_future = None

        # Build result
        self.result_data = {
            "meeting_id": self.config.meeting_id,
            "status": "completed",
            "termination_reason": self.termination_reason,
            "transcript": self._transcript_dicts(),
            "minutes_raw": minutes_raw,
            "artifact_raw": artifact_raw,
            "artifact_goal": self.config.goal.value,
            "personal_reports": {
                aid: {
                    "owner_id": self.agents[aid].owner_id,
                    "content": content,
                }
                for aid, content in reports.items()
            },
        }

    # ── Helpers ─────────────────────────────────────────────────────

    def _meeting_info(self) -> dict:
        return {
            "meeting_id": self.config.meeting_id,
            "topic": self.config.topic,
            "context": self.config.context,
            "goal": self.config.goal.value,
            "termination": self.config.termination.value,
            "max_rounds": self.config.max_rounds,
            "status": self.status.value,
            "agents": list(self.agents.keys()),
            "expected_agents": self.config.expected_agents,
        }

    def _transcript_dicts(self) -> list[dict]:
        return [
            {
                "role": m.role,
                "agent_id": m.agent_id,
                "content": m.content,
                "round_number": m.round_number,
            }
            for m in self.transcript
        ]

    def _format_transcript(self) -> str:
        lines: list[str] = []
        for m in self.transcript:
            if m.role == "system":
                lines.append(f"[System] {m.content}")
            else:
                lines.append(f"[{m.agent_id}] {m.content}")
        return "\n\n".join(lines)


# ── HTTP Routes ─────────────────────────────────────────────────────


def create_app(config: MeetingConfig) -> web.Application:
    """Create an aiohttp app hosting a single meeting room."""
    room = MeetingRoom(config)

    app = web.Application()
    app["room"] = room

    app.router.add_get("/meeting", handle_info)
    app.router.add_post("/meeting/join", handle_join)
    app.router.add_post("/meeting/seal", handle_seal)
    app.router.add_get("/meeting/next", handle_next)
    app.router.add_post("/meeting/respond", handle_respond)
    app.router.add_get("/meeting/result", handle_result)

    return app


async def handle_info(request: web.Request) -> web.Response:
    room: MeetingRoom = request.app["room"]
    return web.json_response(room._meeting_info())


async def handle_join(request: web.Request) -> web.Response:
    room: MeetingRoom = request.app["room"]
    data = await request.json()
    try:
        info = room.join(data["agent_id"], data["owner_id"])
        return web.json_response(info)
    except (ValueError, KeyError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_seal(request: web.Request) -> web.Response:
    room: MeetingRoom = request.app["room"]
    try:
        room.seal()
        return web.json_response({"status": "sealed"})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_next(request: web.Request) -> web.Response:
    """Long-poll: blocks until an action is available for this agent."""
    room: MeetingRoom = request.app["room"]
    agent_id = request.query.get("agent_id")
    if not agent_id:
        return web.json_response({"error": "agent_id required"}, status=400)
    try:
        action = await asyncio.wait_for(
            room.get_next_action(agent_id), timeout=120,
        )
        return web.json_response(action)
    except asyncio.TimeoutError:
        # Client should retry
        return web.json_response({"action": "wait"})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_respond(request: web.Request) -> web.Response:
    room: MeetingRoom = request.app["room"]
    data = await request.json()
    try:
        room.submit_response(data["agent_id"], data["content"])
        return web.json_response({"ok": True})
    except (ValueError, KeyError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_result(request: web.Request) -> web.Response:
    room: MeetingRoom = request.app["room"]
    if room.status != MeetingStatus.COMPLETED:
        return web.json_response(
            {"error": "Meeting not completed yet", "status": room.status.value},
            status=425,
        )
    return web.json_response(room.result_data)


# ── Entry point ─────────────────────────────────────────────────────


async def serve(
    config: MeetingConfig,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> dict | None:
    """Start the meeting room server. Blocks until meeting completes."""
    app = create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    room: MeetingRoom = app["room"]

    print(f"\n{'=' * 50}")
    print(f"  Conclave Meeting Room")
    print(f"  http://{host}:{port}")
    print(f"{'=' * 50}")
    print(f"  Topic: {config.topic}")
    print(f"  Goal:  {config.goal.value}")
    print(f"  Waiting for {config.expected_agents} agents...")
    print(f"{'=' * 50}\n")

    # Wait for meeting to complete
    await room._sealed.wait()
    if room._meeting_task:
        await room._meeting_task

    # Give clients time to receive the final "done" action
    await asyncio.sleep(2)

    await runner.cleanup()
    return room.result_data
