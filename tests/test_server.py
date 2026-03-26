"""Tests for the v0.4 meeting room server, auth, and persistence."""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from conclave.auth import auth_middleware, generate_api_key
from conclave.models import MeetingConfig, MeetingGoal, MeetingStatus, ServerConfig, TerminationMode
from conclave.persistence import MeetingPersistence
from conclave.server import MeetingManager, MeetingRoom, create_app


# ── Helpers ──────────────────────────────────────────────────────────


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


@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def app(tmp_data_dir):
    sc = ServerConfig(api_keys=[], data_dir=tmp_data_dir)
    return create_app(server_config=sc, initial_meeting=_make_config())


@pytest.fixture
def authed_app(tmp_data_dir):
    sc = ServerConfig(api_keys=["test-key-123"], data_dir=tmp_data_dir)
    return create_app(server_config=sc, initial_meeting=_make_config())


# ── MeetingRoom unit tests ──────────────────────────────────────────


def test_meeting_room_join():
    room = MeetingRoom(_make_config())
    info = room.join("agent-a", "alice")
    assert info["meeting_id"] == "test-meeting"
    assert "agent-a" in info["agents"]
    assert room.status == MeetingStatus.PENDING


def test_meeting_room_duplicate_join():
    room = MeetingRoom(_make_config())
    room.join("agent-a", "alice")
    with pytest.raises(ValueError, match="already registered"):
        room.join("agent-a", "alice")


async def test_meeting_room_join_after_seal():
    room = MeetingRoom(_make_config(expected_agents=1))
    room.join("agent-a", "alice")
    with pytest.raises(ValueError, match="already sealed"):
        room.join("agent-b", "bob")
    if room._meeting_task:
        room._meeting_task.cancel()
        try:
            await room._meeting_task
        except (asyncio.CancelledError, Exception):
            pass


def test_meeting_room_seal_empty():
    room = MeetingRoom(_make_config())
    with pytest.raises(ValueError, match="No agents"):
        room.seal()


# ── Auth tests ───────────────────────────────────────────────────────


def test_generate_api_key():
    key = generate_api_key()
    assert len(key) > 20
    assert key != generate_api_key()


async def test_auth_rejects_no_header(aiohttp_client, authed_app):
    client = await aiohttp_client(authed_app)
    resp = await client.get("/meetings")
    assert resp.status == 401


async def test_auth_rejects_bad_key(aiohttp_client, authed_app):
    client = await aiohttp_client(authed_app)
    resp = await client.get("/meetings", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status == 401


async def test_auth_accepts_valid_key(aiohttp_client, authed_app):
    client = await aiohttp_client(authed_app)
    resp = await client.get("/meetings", headers={"Authorization": "Bearer test-key-123"})
    assert resp.status == 200


async def test_no_auth_when_keys_empty(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings")
    assert resp.status == 200


# ── Persistence tests ────────────────────────────────────────────────


def test_persistence_save_load(tmp_data_dir):
    p = MeetingPersistence(tmp_data_dir)
    data = {"meeting_id": "m1", "topic": "Test", "status": "completed", "artifact_goal": "brainstorm"}
    p.save("m1", data)
    assert p.exists("m1")
    loaded = p.load("m1")
    assert loaded["meeting_id"] == "m1"


def test_persistence_list(tmp_data_dir):
    p = MeetingPersistence(tmp_data_dir)
    p.save("m1", {"meeting_id": "m1", "topic": "Alpha"})
    p.save("m2", {"meeting_id": "m2", "topic": "Beta"})
    meetings = p.list_meetings()
    assert len(meetings) == 2


def test_persistence_search(tmp_data_dir):
    p = MeetingPersistence(tmp_data_dir)
    p.save("m1", {"meeting_id": "m1", "topic": "AI brainstorm"})
    p.save("m2", {"meeting_id": "m2", "topic": "Budget review"})
    results = p.list_meetings(search="AI")
    assert len(results) == 1
    assert results[0]["meeting_id"] == "m1"


def test_persistence_not_found(tmp_data_dir):
    p = MeetingPersistence(tmp_data_dir)
    assert p.load("nonexistent") is None
    assert not p.exists("nonexistent")


# ── HTTP endpoint tests (multi-meeting) ──────────────────────────────


async def test_list_meetings(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings")
    assert resp.status == 200
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["meeting_id"] == "test-meeting"


async def test_create_meeting(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/meetings", json={
        "meeting_id": "new-meeting",
        "topic": "New topic",
        "expected_agents": 2,
    })
    assert resp.status == 201
    data = await resp.json()
    assert data["meeting_id"] == "new-meeting"


async def test_create_duplicate_meeting(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/meetings", json={
        "meeting_id": "test-meeting",
        "topic": "Duplicate",
    })
    assert resp.status == 400


async def test_meeting_info(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings/test-meeting")
    assert resp.status == 200
    data = await resp.json()
    assert data["topic"] == "Test topic"


async def test_meeting_not_found(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings/nonexistent")
    assert resp.status == 404


async def test_join_meeting(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/meetings/test-meeting/join", json={
        "agent_id": "a1", "owner_id": "alice",
    })
    assert resp.status == 200
    data = await resp.json()
    assert "a1" in data["agents"]


async def test_history_empty(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings/history")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


# ── Full meeting flow ────────────────────────────────────────────────


async def test_full_meeting_flow(aiohttp_client, tmp_data_dir):
    sc = ServerConfig(api_keys=[], data_dir=tmp_data_dir)
    config = _make_config(expected_agents=2, max_rounds=2)
    app = create_app(server_config=sc, initial_meeting=config)
    client = await aiohttp_client(app)

    # Join
    await client.post("/meetings/test-meeting/join", json={"agent_id": "a1", "owner_id": "alice"})
    await client.post("/meetings/test-meeting/join", json={"agent_id": "a2", "owner_id": "bob"})

    async def simulate_agent(agent_id: str):
        while True:
            resp = await client.get(f"/meetings/test-meeting/next", params={"agent_id": agent_id})
            data = await resp.json()
            action = data.get("action")
            if action == "done":
                return data.get("result")
            elif action in ("speak", "generate"):
                await client.post("/meetings/test-meeting/respond", json={
                    "agent_id": agent_id, "content": f"Response from {agent_id}",
                })
            elif action == "vote":
                await client.post("/meetings/test-meeting/respond", json={
                    "agent_id": agent_id, "content": "YES",
                })
            elif action == "generate_report":
                await client.post("/meetings/test-meeting/respond", json={
                    "agent_id": agent_id, "content": f"Report from {agent_id}",
                })
            elif action == "wait":
                continue

    results = await asyncio.gather(simulate_agent("a1"), simulate_agent("a2"))
    result = results[0]
    assert result is not None
    assert result["meeting_id"] == "test-meeting"
    assert result["status"] == "completed"
    assert "Vote passed" in result["termination_reason"]

    # Verify persistence
    p = MeetingPersistence(tmp_data_dir)
    assert p.exists("test-meeting")
    saved = p.load("test-meeting")
    assert saved["meeting_id"] == "test-meeting"
