"""Meeting room server — the shared conclave space (v0.4).

Multi-meeting server with authentication, persistence, and discovery.
Agents create meetings, join them, and get results — all via HTTP.

Privacy: only utterances pass through the server. Personas stay on clients.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
from typing import Callable

from aiohttp import web

from conclave.auth import auth_middleware, generate_api_key
from conclave.models import (
    MeetingConfig,
    MeetingStatus,
    Message,
    ServerConfig,
    TerminationMode,
)
from conclave.output import ARTIFACT_PROMPTS, MINUTES_PROMPT
from conclave.persistence import MeetingPersistence

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

    def __init__(
        self,
        config: MeetingConfig,
        on_complete: Callable[[MeetingRoom], None] | None = None,
    ) -> None:
        self.config = config
        self.agents: dict[str, AgentSlot] = {}
        self.transcript: list[Message] = []
        self.current_round: int = 0
        self.status: MeetingStatus = MeetingStatus.PENDING
        self.termination_reason: str | None = None
        self.result_data: dict | None = None

        self._sealed = asyncio.Event()
        self._meeting_task: asyncio.Task | None = None
        self._on_complete = on_complete

    # ── Registration ────────────────────────────────────────────────

    def join(self, agent_id: str, owner_id: str) -> dict:
        if self._sealed.is_set():
            raise ValueError("Meeting already sealed, cannot join")
        if agent_id in self.agents:
            raise ValueError(f"Agent '{agent_id}' already registered")

        self.agents[agent_id] = AgentSlot(agent_id, owner_id)
        logger.info(
            "Agent '%s' (owner: %s) joined '%s' — %d/%d",
            agent_id, owner_id, self.config.meeting_id,
            len(self.agents), self.config.expected_agents,
        )

        if (
            self.config.expected_agents > 0
            and len(self.agents) >= self.config.expected_agents
        ):
            self.seal()

        return self.meeting_info()

    def seal(self) -> None:
        if self._sealed.is_set():
            return
        if not self.agents:
            raise ValueError("No agents registered")
        self._sealed.set()
        self._meeting_task = asyncio.create_task(self._run_meeting())
        logger.info("Meeting '%s' sealed with %d agents", self.config.meeting_id, len(self.agents))

    # ── Client interaction ──────────────────────────────────────────

    async def get_next_action(self, agent_id: str) -> dict:
        if agent_id not in self.agents:
            raise ValueError(f"Unknown agent: {agent_id}")
        return await self.agents[agent_id].action_queue.get()

    def submit_response(self, agent_id: str, content: str) -> None:
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
                self.termination_reason = f"Max rounds ({self.config.max_rounds}) reached"
            await self._collect_outputs()
        except Exception:
            logger.exception("Meeting '%s' failed", self.config.meeting_id)
            self.termination_reason = "Error during meeting"

        self.status = MeetingStatus.COMPLETED

        if self._on_complete:
            self._on_complete(self)

        for slot in self.agents.values():
            await slot.action_queue.put({"action": "done", "result": self.result_data})

        logger.info("Meeting '%s' completed: %s", self.config.meeting_id, self.termination_reason)

    async def _run_round(self) -> bool:
        agent_ids = list(self.agents.keys())
        random.shuffle(agent_ids)
        logger.info("Round %d — order: %s", self.current_round, agent_ids)
        loop = asyncio.get_running_loop()

        for agent_id in agent_ids:
            slot = self.agents[agent_id]
            slot.response_future = loop.create_future()
            await slot.action_queue.put({
                "action": "speak",
                "transcript": self._transcript_dicts(),
                "round_number": self.current_round,
            })
            utterance = await slot.response_future
            slot.response_future = None
            self.transcript.append(Message(
                role="agent", agent_id=agent_id,
                content=utterance, round_number=self.current_round,
            ))
            logger.debug("[%s] %s", agent_id, utterance[:100])

        # Voting
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
            votes[agent_id] = raw.strip().upper().startswith("YES") if isinstance(raw, str) else bool(raw)

        yes_count = sum(1 for v in votes.values() if v)
        total = len(votes)
        logger.info("Round %d votes: %d/%d", self.current_round, yes_count, total)

        if self.config.termination == TerminationMode.SUPERMAJORITY_VOTE:
            terminate = yes_count >= math.ceil(total * 2 / 3)
        else:
            terminate = yes_count == total

        if terminate:
            self.termination_reason = f"Vote passed ({yes_count}/{total}) after round {self.current_round}"
            return False
        return True

    async def _collect_outputs(self) -> None:
        transcript_text = self._format_transcript()
        first_id = next(iter(self.agents))
        first_slot = self.agents[first_id]
        loop = asyncio.get_running_loop()

        # Minutes
        first_slot.response_future = loop.create_future()
        await first_slot.action_queue.put({
            "action": "generate",
            "prompt": f"{MINUTES_PROMPT}\n\nMeeting topic: {self.config.topic}\n\nTranscript:\n{transcript_text}",
            "output_type": "minutes",
        })
        minutes_raw = await first_slot.response_future
        first_slot.response_future = None

        # Artifact
        artifact_raw = None
        artifact_prompt_text = ARTIFACT_PROMPTS.get(self.config.goal)
        if artifact_prompt_text:
            first_slot.response_future = loop.create_future()
            await first_slot.action_queue.put({
                "action": "generate",
                "prompt": f"{artifact_prompt_text}\n\nMeeting topic: {self.config.topic}\nMeeting context: {self.config.context}\n\nTranscript:\n{transcript_text}",
                "output_type": "artifact",
            })
            artifact_raw = await first_slot.response_future
            first_slot.response_future = None

        # Personal reports (concurrent)
        report_futures: dict[str, asyncio.Future[str]] = {}
        for agent_id, slot in self.agents.items():
            slot.response_future = loop.create_future()
            await slot.action_queue.put({"action": "generate_report", "transcript": self._transcript_dicts()})
            report_futures[agent_id] = slot.response_future

        reports: dict[str, str] = {}
        for agent_id, future in report_futures.items():
            reports[agent_id] = await future
            self.agents[agent_id].response_future = None

        self.result_data = {
            "meeting_id": self.config.meeting_id,
            "topic": self.config.topic,
            "status": "completed",
            "termination_reason": self.termination_reason,
            "transcript": self._transcript_dicts(),
            "minutes_raw": minutes_raw,
            "artifact_raw": artifact_raw,
            "artifact_goal": self.config.goal.value,
            "personal_reports": {
                aid: {"owner_id": self.agents[aid].owner_id, "content": content}
                for aid, content in reports.items()
            },
        }

    # ── Helpers ─────────────────────────────────────────────────────

    def meeting_info(self) -> dict:
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
            {"role": m.role, "agent_id": m.agent_id, "content": m.content, "round_number": m.round_number}
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


# ── Meeting Manager ─────────────────────────────────────────────────


class MeetingManager:
    """Manages multiple concurrent meetings with persistence."""

    def __init__(
        self,
        server_config: ServerConfig | None = None,
    ) -> None:
        self.config = server_config or ServerConfig()
        self.meetings: dict[str, MeetingRoom] = {}
        self.persistence = MeetingPersistence(self.config.data_dir)
        self._lock = asyncio.Lock()

    async def create_meeting(self, config: MeetingConfig) -> MeetingRoom:
        async with self._lock:
            if config.meeting_id in self.meetings:
                raise ValueError(f"Meeting '{config.meeting_id}' already exists")
            if len(self.meetings) >= self.config.max_meetings:
                raise ValueError(f"Max concurrent meetings ({self.config.max_meetings}) reached")

            room = MeetingRoom(config, on_complete=self._on_meeting_complete)
            self.meetings[config.meeting_id] = room
            logger.info("Created meeting '%s': %s", config.meeting_id, config.topic)
            return room

    def get_meeting(self, meeting_id: str) -> MeetingRoom:
        try:
            return self.meetings[meeting_id]
        except KeyError:
            raise ValueError(f"Meeting '{meeting_id}' not found")

    def list_meetings(
        self, status: str = "", search: str = "",
    ) -> list[dict]:
        results = []
        for room in self.meetings.values():
            info = room.meeting_info()
            if status and info["status"] != status:
                continue
            if search and search.lower() not in info["topic"].lower():
                continue
            results.append(info)
        return results

    def _on_meeting_complete(self, room: MeetingRoom) -> None:
        if room.result_data:
            self.persistence.save(room.config.meeting_id, room.result_data)


# ── HTTP Routes ─────────────────────────────────────────────────────


def _get_room(request: web.Request) -> MeetingRoom:
    """Extract MeetingRoom from request path."""
    manager: MeetingManager = request.app["manager"]
    meeting_id = request.match_info["meeting_id"]
    return manager.get_meeting(meeting_id)


def create_app(
    server_config: ServerConfig | None = None,
    initial_meeting: MeetingConfig | None = None,
) -> web.Application:
    """Create an aiohttp app with multi-meeting support and auth."""
    sc = server_config or ServerConfig()

    # Resolve API keys: config > env > auto-generate
    api_keys = list(sc.api_keys)
    if not api_keys:
        env_keys = os.environ.get("CONCLAVE_API_KEYS", "")
        if env_keys:
            api_keys = [k.strip() for k in env_keys.split(",") if k.strip()]

    middlewares = []
    if api_keys:
        middlewares.append(auth_middleware(api_keys))

    app = web.Application(middlewares=middlewares)
    app["api_keys"] = api_keys

    # Manager
    manager = MeetingManager(sc)
    app["manager"] = manager

    # Routes: multi-meeting
    app.router.add_post("/meetings", handle_create_meeting)
    app.router.add_get("/meetings", handle_list_meetings)
    app.router.add_get("/meetings/history", handle_history)
    app.router.add_get("/meetings/history/{meeting_id}", handle_history_detail)
    app.router.add_get("/meetings/{meeting_id}", handle_meeting_info)
    app.router.add_post("/meetings/{meeting_id}/join", handle_join)
    app.router.add_post("/meetings/{meeting_id}/seal", handle_seal)
    app.router.add_get("/meetings/{meeting_id}/next", handle_next)
    app.router.add_post("/meetings/{meeting_id}/respond", handle_respond)
    app.router.add_get("/meetings/{meeting_id}/result", handle_result)

    # Create initial meeting if provided (for `conclave serve meeting.yaml`)
    if initial_meeting:
        async def _create_initial(app_):
            await manager.create_meeting(initial_meeting)
        app.on_startup.append(_create_initial)

    return app


async def handle_create_meeting(request: web.Request) -> web.Response:
    manager: MeetingManager = request.app["manager"]
    data = await request.json()
    try:
        config = MeetingConfig.model_validate(data)
        room = await manager.create_meeting(config)
        return web.json_response(room.meeting_info(), status=201)
    except (ValueError, Exception) as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_list_meetings(request: web.Request) -> web.Response:
    manager: MeetingManager = request.app["manager"]
    status = request.query.get("status", "")
    search = request.query.get("search", "")
    return web.json_response(manager.list_meetings(status=status, search=search))


async def handle_meeting_info(request: web.Request) -> web.Response:
    try:
        room = _get_room(request)
        return web.json_response(room.meeting_info())
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)


async def handle_join(request: web.Request) -> web.Response:
    try:
        room = _get_room(request)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    data = await request.json()
    try:
        info = room.join(data["agent_id"], data["owner_id"])
        return web.json_response(info)
    except (ValueError, KeyError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_seal(request: web.Request) -> web.Response:
    try:
        room = _get_room(request)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    try:
        room.seal()
        return web.json_response({"status": "sealed"})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_next(request: web.Request) -> web.Response:
    try:
        room = _get_room(request)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    agent_id = request.query.get("agent_id")
    if not agent_id:
        return web.json_response({"error": "agent_id required"}, status=400)
    try:
        action = await asyncio.wait_for(room.get_next_action(agent_id), timeout=120)
        return web.json_response(action)
    except asyncio.TimeoutError:
        return web.json_response({"action": "wait"})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_respond(request: web.Request) -> web.Response:
    try:
        room = _get_room(request)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    data = await request.json()
    try:
        room.submit_response(data["agent_id"], data["content"])
        return web.json_response({"ok": True})
    except (ValueError, KeyError) as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_result(request: web.Request) -> web.Response:
    try:
        room = _get_room(request)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    if room.status != MeetingStatus.COMPLETED:
        return web.json_response(
            {"error": "Meeting not completed yet", "status": room.status.value},
            status=425,
        )
    return web.json_response(room.result_data)


async def handle_history(request: web.Request) -> web.Response:
    manager: MeetingManager = request.app["manager"]
    search = request.query.get("search", "")
    limit = int(request.query.get("limit", "50"))
    return web.json_response(
        manager.persistence.list_meetings(limit=limit, search=search)
    )


async def handle_history_detail(request: web.Request) -> web.Response:
    manager: MeetingManager = request.app["manager"]
    meeting_id = request.match_info["meeting_id"]
    result = manager.persistence.load(meeting_id)
    if result is None:
        return web.json_response({"error": "Meeting not found in history"}, status=404)
    return web.json_response(result)


# ── Entry point ─────────────────────────────────────────────────────


async def serve(
    host: str = "0.0.0.0",
    port: int = 8080,
    server_config: ServerConfig | None = None,
    initial_meeting: MeetingConfig | None = None,
) -> None:
    """Start the meeting room server. Runs until interrupted."""
    sc = server_config or ServerConfig()

    # Auto-generate API key if none configured
    api_keys = list(sc.api_keys)
    env_keys = os.environ.get("CONCLAVE_API_KEYS", "")
    if not api_keys and env_keys:
        api_keys = [k.strip() for k in env_keys.split(",") if k.strip()]
    if not api_keys:
        api_keys = [generate_api_key()]
    sc = sc.model_copy(update={"api_keys": api_keys})

    app = create_app(server_config=sc, initial_meeting=initial_meeting)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print(f"\n{'=' * 50}")
    print(f"  Conclave Server v0.4")
    print(f"  http://{host}:{port}")
    print(f"{'=' * 50}")
    print(f"  API Key: {api_keys[0]}")
    if initial_meeting:
        print(f"  Initial meeting: {initial_meeting.meeting_id}")
        print(f"  Topic: {initial_meeting.topic}")
        print(f"  Waiting for {initial_meeting.expected_agents} agents...")
    else:
        print(f"  No initial meeting — create via POST /meetings")
    print(f"  Data dir: {sc.data_dir}")
    print(f"{'=' * 50}\n")

    # Run until interrupted
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.cleanup()
