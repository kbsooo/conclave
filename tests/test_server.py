"""Tests for the v0.2 meeting room server and client."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient

from conclave.models import MeetingConfig, MeetingGoal, MeetingStatus, TerminationMode
from conclave.server import MeetingRoom, create_app


# ── MeetingRoom unit tests ──────────────────────────────────────────


def _make_config(**overrides) -> MeetingConfig:
    defaults = dict(
        meeting_id="test-meeting",
        topic="Test topic",
        context="Test context",
        goal=MeetingGoal.BRAINSTORM,
        termination=TerminationMode.SUPERMAJORITY_VOTE,
        max_rounds=3,
        expected_agents=2,
    )
    defaults.update(overrides)
    return MeetingConfig(**defaults)


def test_meeting_room_join():
    config = _make_config()
    room = MeetingRoom(config)

    info = room.join("agent-a", "alice")
    assert info["meeting_id"] == "test-meeting"
    assert "agent-a" in info["agents"]
    assert room.status == MeetingStatus.PENDING


def test_meeting_room_duplicate_join():
    config = _make_config()
    room = MeetingRoom(config)

    room.join("agent-a", "alice")
    with pytest.raises(ValueError, match="already registered"):
        room.join("agent-a", "alice")


async def test_meeting_room_join_after_seal():
    config = _make_config(expected_agents=1)
    room = MeetingRoom(config)

    room.join("agent-a", "alice")  # auto-seals at 1

    with pytest.raises(ValueError, match="already sealed"):
        room.join("agent-b", "bob")

    # Clean up the background meeting task
    if room._meeting_task:
        room._meeting_task.cancel()
        try:
            await room._meeting_task
        except (asyncio.CancelledError, Exception):
            pass


def test_meeting_room_seal_empty():
    config = _make_config()
    room = MeetingRoom(config)

    with pytest.raises(ValueError, match="No agents"):
        room.seal()


def test_meeting_room_meeting_info():
    config = _make_config()
    room = MeetingRoom(config)
    room.join("agent-a", "alice")

    info = room._meeting_info()
    assert info["topic"] == "Test topic"
    assert info["goal"] == "brainstorm"
    assert info["expected_agents"] == 2


# ── HTTP endpoint tests ─────────────────────────────────────────────


@pytest.fixture
def app():
    config = _make_config(expected_agents=2)
    return create_app(config)


async def test_handle_info(aiohttp_client, app):
    client: TestClient = await aiohttp_client(app)
    resp = await client.get("/meeting")
    assert resp.status == 200
    data = await resp.json()
    assert data["topic"] == "Test topic"
    assert data["status"] == "pending"


async def test_handle_join(aiohttp_client, app):
    client: TestClient = await aiohttp_client(app)

    resp = await client.post("/meeting/join", json={
        "agent_id": "agent-a", "owner_id": "alice",
    })
    assert resp.status == 200
    data = await resp.json()
    assert "agent-a" in data["agents"]


async def test_handle_join_duplicate(aiohttp_client, app):
    client: TestClient = await aiohttp_client(app)

    await client.post("/meeting/join", json={
        "agent_id": "agent-a", "owner_id": "alice",
    })
    resp = await client.post("/meeting/join", json={
        "agent_id": "agent-a", "owner_id": "alice",
    })
    assert resp.status == 400


async def test_handle_seal_empty(aiohttp_client, app):
    client: TestClient = await aiohttp_client(app)
    resp = await client.post("/meeting/seal")
    assert resp.status == 400


async def test_handle_result_before_done(aiohttp_client, app):
    client: TestClient = await aiohttp_client(app)
    resp = await client.get("/meeting/result")
    assert resp.status == 425


# ── Full meeting flow (server + simulated clients) ──────────────────


async def test_full_meeting_flow(aiohttp_client):
    """Simulate a complete meeting with two mock agents via HTTP."""
    config = _make_config(expected_agents=2, max_rounds=2)
    app = create_app(config)
    client: TestClient = await aiohttp_client(app)

    # Both agents join → auto-seals
    await client.post("/meeting/join", json={"agent_id": "a1", "owner_id": "alice"})
    await client.post("/meeting/join", json={"agent_id": "a2", "owner_id": "bob"})

    # Simulate both agents participating
    async def simulate_agent(agent_id: str):
        while True:
            resp = await client.get("/meeting/next", params={"agent_id": agent_id})
            data = await resp.json()
            action = data.get("action")

            if action == "done":
                return data.get("result")
            elif action == "speak":
                await client.post("/meeting/respond", json={
                    "agent_id": agent_id,
                    "content": f"Hello from {agent_id}",
                })
            elif action == "vote":
                # Always vote YES so meeting ends after round 1
                await client.post("/meeting/respond", json={
                    "agent_id": agent_id,
                    "content": "YES",
                })
            elif action == "generate":
                await client.post("/meeting/respond", json={
                    "agent_id": agent_id,
                    "content": f"Generated {data.get('output_type', 'output')} content",
                })
            elif action == "generate_report":
                await client.post("/meeting/respond", json={
                    "agent_id": agent_id,
                    "content": f"Report for {agent_id}",
                })
            elif action == "wait":
                continue

    # Run both agents concurrently
    results = await asyncio.gather(
        simulate_agent("a1"),
        simulate_agent("a2"),
    )

    # Both should get the same result
    result = results[0]
    assert result is not None
    assert result["meeting_id"] == "test-meeting"
    assert result["status"] == "completed"
    assert "Vote passed" in result["termination_reason"]
    assert len(result["transcript"]) > 0
    assert "a1" in result["personal_reports"]
    assert "a2" in result["personal_reports"]
