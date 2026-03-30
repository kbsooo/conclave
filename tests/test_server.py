"""Tests for the v0.6 meeting room server — auth, persistence, templates, chains, SSE, reconnection."""

from __future__ import annotations

import asyncio
import json
import tempfile

import pytest

from conclave.auth import generate_api_key
from conclave.models import MeetingConfig, MeetingGoal, MeetingStatus, MeetingTemplate, ServerConfig, TerminationMode
from conclave.persistence import MeetingPersistence, TemplatePersistence
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
    # Before seal, duplicate join raises
    with pytest.raises(ValueError, match="already registered"):
        room.join("agent-a", "alice")


async def test_meeting_room_join_after_seal():
    room = MeetingRoom(_make_config(expected_agents=1))
    room.join("agent-a", "alice")
    # Sealed — new agent cannot join
    with pytest.raises(ValueError, match="already sealed"):
        room.join("agent-b", "bob")
    if room._meeting_task:
        room._meeting_task.cancel()
        try:
            await room._meeting_task
        except (asyncio.CancelledError, Exception):
            pass


async def test_meeting_room_reconnect():
    room = MeetingRoom(_make_config(expected_agents=1))
    room.join("agent-a", "alice")
    # After seal, same agent can reconnect
    info = room.join("agent-a", "alice")
    assert info.get("reconnected") is True
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
    assert len(p.list_meetings()) == 2


def test_persistence_search(tmp_data_dir):
    p = MeetingPersistence(tmp_data_dir)
    p.save("m1", {"meeting_id": "m1", "topic": "AI brainstorm"})
    p.save("m2", {"meeting_id": "m2", "topic": "Budget review"})
    results = p.list_meetings(search="AI")
    assert len(results) == 1


def test_persistence_not_found(tmp_data_dir):
    p = MeetingPersistence(tmp_data_dir)
    assert p.load("nonexistent") is None


# ── Template persistence tests ───────────────────────────────────────


def test_template_save_load(tmp_data_dir):
    tp = TemplatePersistence(tmp_data_dir)
    data = {"template_id": "brainstorm-3", "name": "3-way brainstorm", "goal": "brainstorm"}
    tp.save("brainstorm-3", data)
    loaded = tp.load("brainstorm-3")
    assert loaded["name"] == "3-way brainstorm"


def test_template_list(tmp_data_dir):
    tp = TemplatePersistence(tmp_data_dir)
    tp.save("t1", {"template_id": "t1", "name": "Template 1"})
    tp.save("t2", {"template_id": "t2", "name": "Template 2"})
    assert len(tp.list_templates()) == 2


def test_template_delete(tmp_data_dir):
    tp = TemplatePersistence(tmp_data_dir)
    tp.save("t1", {"template_id": "t1", "name": "Template 1"})
    assert tp.delete("t1") is True
    assert tp.load("t1") is None


# ── HTTP endpoint tests ──────────────────────────────────────────────


async def test_list_meetings(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings")
    assert resp.status == 200
    data = await resp.json()
    assert len(data) == 1


async def test_create_meeting(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/meetings", json={
        "meeting_id": "new-meeting", "topic": "New topic", "expected_agents": 2,
    })
    assert resp.status == 201


async def test_create_duplicate_meeting(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/meetings", json={"meeting_id": "test-meeting", "topic": "Dup"})
    assert resp.status == 400


async def test_meeting_not_found(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/meetings/nonexistent")
    assert resp.status == 404


async def test_join_meeting(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/meetings/test-meeting/join", json={"agent_id": "a1", "owner_id": "alice"})
    assert resp.status == 200
    data = await resp.json()
    assert "a1" in data["agents"]


# ── Template HTTP tests ──────────────────────────────────────────────


async def test_save_and_list_templates(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/templates", json={
        "template_id": "brainstorm-3",
        "name": "3-way brainstorm",
        "goal": "brainstorm",
        "expected_agents": 3,
    })
    assert resp.status == 201

    resp = await client.get("/templates")
    templates = await resp.json()
    assert any(t["template_id"] == "brainstorm-3" for t in templates)


async def test_get_template(aiohttp_client, app):
    client = await aiohttp_client(app)
    await client.post("/templates", json={
        "template_id": "t1", "name": "Test", "goal": "brainstorm", "expected_agents": 2,
    })
    resp = await client.get("/templates/t1")
    assert resp.status == 200
    data = await resp.json()
    assert data["name"] == "Test"


async def test_create_from_template(aiohttp_client, app):
    client = await aiohttp_client(app)
    await client.post("/templates", json={
        "template_id": "decision-panel", "name": "Decision", "goal": "decision",
        "expected_agents": 3, "max_rounds": 5,
    })
    resp = await client.post("/meetings/from-template", json={
        "template_id": "decision-panel", "topic": "Which DB?",
    })
    assert resp.status == 201
    data = await resp.json()
    assert data["goal"] == "decision"
    assert data["expected_agents"] == 3


# ── Full meeting flow (with persistence) ─────────────────────────────


async def test_full_meeting_flow(aiohttp_client, tmp_data_dir):
    sc = ServerConfig(api_keys=[], data_dir=tmp_data_dir)
    config = _make_config(expected_agents=2, max_rounds=2)
    app = create_app(server_config=sc, initial_meeting=config)
    client = await aiohttp_client(app)

    await client.post("/meetings/test-meeting/join", json={"agent_id": "a1", "owner_id": "alice"})
    await client.post("/meetings/test-meeting/join", json={"agent_id": "a2", "owner_id": "bob"})

    async def simulate_agent(agent_id: str):
        while True:
            resp = await client.get("/meetings/test-meeting/next", params={"agent_id": agent_id})
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
    assert result["status"] == "completed"
    assert "Vote passed" in result["termination_reason"]

    # Verify persistence
    p = MeetingPersistence(tmp_data_dir)
    assert p.exists("test-meeting")


# ── Chain test ───────────────────────────────────────────────────────


async def test_chain_from_loads_prior_context(aiohttp_client, tmp_data_dir):
    sc = ServerConfig(api_keys=[], data_dir=tmp_data_dir)
    app = create_app(server_config=sc)
    client = await aiohttp_client(app)

    # Save a fake prior meeting result
    p = MeetingPersistence(tmp_data_dir)
    p.save("prior-meeting", {
        "meeting_id": "prior-meeting",
        "minutes_raw": "We decided to use PostgreSQL.",
        "status": "completed",
    })

    # Create a meeting chained from the prior
    resp = await client.post("/meetings", json={
        "meeting_id": "chained-meeting",
        "topic": "Implementation plan",
        "chain_from": "prior-meeting",
        "chain_context_mode": "minutes",
        "expected_agents": 2,
    })
    assert resp.status == 201
    data = await resp.json()
    assert "PostgreSQL" in data["context"]


# ── SSE event test ───────────────────────────────────────────────────


async def test_event_emission():
    room = MeetingRoom(_make_config())
    queue = room.subscribe()
    room._emit("test_event", {"key": "value"})
    event = queue.get_nowait()
    assert event["event"] == "test_event"
    assert event["data"]["key"] == "value"
    room.unsubscribe(queue)
