"""Conclave MCP server — expose meetings as tools for AI agents.

Tools:
  conclave_run     — Run a complete meeting locally (v0.1)
  conclave_host    — Start a meeting room server (v0.4)
  conclave_join    — Join a remote meeting (v0.4)
  conclave_create  — Create a meeting on a running server
  conclave_list    — List meetings on a server
  conclave_status  — Get meeting status
  conclave_history — Query past meeting results

Configure in Claude Code:
    { "mcpServers": { "conclave": { "command": "conclave", "args": ["mcp"] } } }
"""

from __future__ import annotations

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "conclave",
    instructions=(
        "Multi-agent meeting system. "
        "Run sealed meetings where AI agents debate, brainstorm, "
        "write code, draft documents, or make decisions."
    ),
)


def _redirect_stdout():
    sys.stdout = sys.stderr


def _format_result(result) -> str:
    """Format a v0.1 MeetingResult object."""
    out: list[str] = []
    out.append(f"**Goal:** {result.minutes.summary[:50]}... | **Ended:** {result.termination_reason}\n")
    out.append("## Minutes")
    out.append(result.minutes.summary)
    if result.minutes.key_points:
        out.append("\n### Key Points")
        for p in result.minutes.key_points:
            out.append(f"- {p}")
    if result.minutes.decisions:
        out.append("\n### Decisions")
        for d in result.minutes.decisions:
            out.append(f"- {d}")
    if result.artifact:
        out.append(f"\n## Artifact ({result.artifact.goal.value})")
        out.append(result.artifact.content)
    for owner_id, report in result.personal_reports.items():
        out.append(f"\n## Report — {report.agent_id}")
        out.append(report.summary)
    return "\n".join(out)


def _format_server_result(result: dict) -> str:
    """Format a v0.4 result dict."""
    out: list[str] = []
    out.append(f"**Ended:** {result.get('termination_reason', 'Unknown')}\n")
    minutes = result.get("minutes_raw")
    if minutes:
        out.append("## Minutes")
        out.append(minutes)
    artifact = result.get("artifact_raw")
    if artifact:
        out.append(f"\n## Artifact ({result.get('artifact_goal', 'unknown')})")
        out.append(artifact)
    for aid, info in result.get("personal_reports", {}).items():
        out.append(f"\n## Report — {aid}")
        out.append(info.get("content", ""))
    return "\n".join(out)


# ── Core tools ──────────────────────────────────────────────────────


@mcp.tool()
async def conclave_run(
    topic: str,
    perspectives: str,
    context: str = "",
    goal: str = "brainstorm",
    max_rounds: int = 5,
    command: str = "claude",
) -> str:
    """Run a sealed multi-agent meeting with different perspectives.

    Args:
        topic: The meeting topic or question to discuss.
        perspectives: JSON array, e.g.:
            [{"name": "optimist", "instruction": "focus on opportunities"},
             {"name": "critic", "instruction": "find risks"}]
        context: Shared background information.
        goal: "brainstorm", "code", "document", or "decision".
        max_rounds: Max discussion rounds.
        command: CLI agent — "claude", "gemini", "codex", "openclaw".
    """
    _redirect_stdout()
    from conclave.models import AgentConfig, MeetingConfig, MeetingGoal, TerminationMode
    from conclave.orchestrator import MeetingOrchestrator

    try:
        persp_list = json.loads(perspectives)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON — {e}"
    if not persp_list:
        return "Error: At least one perspective required."
    try:
        meeting_goal = MeetingGoal(goal)
    except ValueError:
        return f"Error: Invalid goal '{goal}'. Use: brainstorm, code, document, decision"

    agents = [
        AgentConfig(
            agent_id=p.get("name", f"agent-{i}"), owner_id="mcp-user",
            backend="cli", command=command, instruction=p.get("instruction", ""),
        )
        for i, p in enumerate(persp_list)
    ]
    config = MeetingConfig(
        meeting_id=f"mcp-{topic[:30].replace(' ', '-').lower()}",
        topic=topic, context=context, goal=meeting_goal,
        agents=agents, termination=TerminationMode.SUPERMAJORITY_VOTE, max_rounds=max_rounds,
    )
    result = await MeetingOrchestrator(config).run()
    return f"# Meeting: {topic}\n{_format_result(result)}"


@mcp.tool()
async def conclave_host(
    topic: str,
    context: str = "",
    goal: str = "brainstorm",
    expected_agents: int = 3,
    port: int = 8080,
    max_rounds: int = 10,
) -> str:
    """Host a meeting room server that remote agents can join.

    Args:
        topic: The meeting topic.
        context: Shared background.
        goal: "brainstorm", "code", "document", or "decision".
        expected_agents: Auto-start after this many join.
        port: Server port.
        max_rounds: Max rounds.
    """
    _redirect_stdout()
    from aiohttp import web
    from conclave.auth import generate_api_key
    from conclave.models import MeetingConfig, MeetingGoal, ServerConfig, TerminationMode
    from conclave.server import create_app

    try:
        meeting_goal = MeetingGoal(goal)
    except ValueError:
        return f"Error: Invalid goal '{goal}'."

    api_key = generate_api_key()
    meeting_id = f"mcp-hosted-{topic[:20].replace(' ', '-').lower()}"

    meeting_config = MeetingConfig(
        meeting_id=meeting_id, topic=topic, context=context,
        goal=meeting_goal, termination=TerminationMode.SUPERMAJORITY_VOTE,
        max_rounds=max_rounds, expected_agents=expected_agents,
    )
    sc = ServerConfig(api_keys=[api_key])
    app = create_app(server_config=sc, initial_meeting=meeting_config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    _active_servers[port] = runner

    url = f"http://localhost:{port}"
    return (
        f"Meeting room open at {url}\n"
        f"Meeting ID: {meeting_id}\n"
        f"API Key: {api_key}\n"
        f"Topic: {topic} | Goal: {goal}\n"
        f"Waiting for {expected_agents} agents...\n\n"
        f"Join with:\n"
        f'  conclave_join(server_url="{url}", meeting_id="{meeting_id}", '
        f'agent_id="my-agent", api_key="{api_key}")'
    )


@mcp.tool()
async def conclave_join(
    server_url: str,
    agent_id: str,
    meeting_id: str = "",
    instruction: str = "",
    api_key: str = "",
    owner_id: str = "mcp-user",
    command: str = "claude",
) -> str:
    """Join a remote meeting and participate until it ends.

    Args:
        server_url: Server URL (e.g., http://localhost:8080).
        agent_id: Unique agent name.
        meeting_id: Meeting to join (empty = first pending).
        instruction: Private guidance (never shared).
        api_key: Server API key.
        owner_id: Who this agent represents.
        command: CLI agent — "claude", "gemini", "codex", "openclaw".
    """
    _redirect_stdout()
    from conclave.client import MeetingClient
    from conclave.models import AgentConfig

    agent_config = AgentConfig(
        agent_id=agent_id, owner_id=owner_id,
        backend="cli", command=command, instruction=instruction,
    )
    client = MeetingClient(server_url, agent_config, meeting_id=meeting_id, api_key=api_key)
    result = await client.run()
    if not result:
        return "Meeting ended but no results returned."
    return f"# Meeting Results\n{_format_server_result(result)}"


# ── Discovery tools ─────────────────────────────────────────────────


@mcp.tool()
async def conclave_create(
    server_url: str,
    topic: str,
    context: str = "",
    goal: str = "brainstorm",
    expected_agents: int = 3,
    max_rounds: int = 10,
    api_key: str = "",
) -> str:
    """Create a new meeting on a running Conclave server.

    Args:
        server_url: Server URL.
        topic: Meeting topic.
        context: Shared background.
        goal: "brainstorm", "code", "document", or "decision".
        expected_agents: Auto-start count.
        max_rounds: Max rounds.
        api_key: Server API key.
    """
    _redirect_stdout()
    import aiohttp
    from conclave.models import MeetingConfig, MeetingGoal, TerminationMode

    try:
        meeting_goal = MeetingGoal(goal)
    except ValueError:
        return f"Error: Invalid goal '{goal}'."

    config = MeetingConfig(
        meeting_id=f"mcp-{topic[:30].replace(' ', '-').lower()}",
        topic=topic, context=context, goal=meeting_goal,
        termination=TerminationMode.SUPERMAJORITY_VOTE,
        max_rounds=max_rounds, expected_agents=expected_agents,
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            f"{server_url.rstrip('/')}/meetings",
            json=config.model_dump(mode="json"),
        ) as resp:
            data = await resp.json()
            if resp.status == 201:
                return (
                    f"Created meeting: {data['meeting_id']}\n"
                    f"Topic: {data['topic']}\n"
                    f"Goal: {data['goal']}\n"
                    f"Waiting for {data['expected_agents']} agents"
                )
            return f"Error: {data.get('error', 'unknown')}"


@mcp.tool()
async def conclave_list(
    server_url: str,
    status: str = "",
    search: str = "",
    api_key: str = "",
) -> str:
    """List meetings on a Conclave server.

    Args:
        server_url: Server URL.
        status: Filter — "pending", "in_progress", "completed".
        search: Search topic text.
        api_key: Server API key.
    """
    _redirect_stdout()
    import aiohttp
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    params = {}
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{server_url.rstrip('/')}/meetings", params=params) as resp:
            meetings = await resp.json()
    if not meetings:
        return "No meetings found."
    lines = ["| Meeting ID | Status | Agents | Topic |", "| --- | --- | --- | --- |"]
    for m in meetings:
        agents = m.get("agents", [])
        lines.append(f"| {m['meeting_id']} | {m['status']} | {len(agents)}/{m.get('expected_agents','?')} | {m['topic']} |")
    return "\n".join(lines)


@mcp.tool()
async def conclave_status(
    server_url: str,
    meeting_id: str,
    api_key: str = "",
) -> str:
    """Get the status of a specific meeting.

    Args:
        server_url: Server URL.
        meeting_id: The meeting ID.
        api_key: Server API key.
    """
    _redirect_stdout()
    import aiohttp
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{server_url.rstrip('/')}/meetings/{meeting_id}") as resp:
            data = await resp.json()
    if "error" in data:
        return f"Error: {data['error']}"
    return (
        f"Meeting: {data['meeting_id']}\n"
        f"Topic: {data['topic']}\n"
        f"Goal: {data['goal']}\n"
        f"Status: {data['status']}\n"
        f"Agents: {', '.join(data.get('agents', [])) or '(none)'}\n"
        f"Expected: {data.get('expected_agents', '?')}"
    )


@mcp.tool()
async def conclave_history(
    meeting_id: str = "",
    search: str = "",
    data_dir: str = "~/.conclave",
) -> str:
    """Query past meeting results from local storage.

    Args:
        meeting_id: Specific meeting to retrieve (empty = list recent).
        search: Search by topic.
        data_dir: Data directory (default: ~/.conclave).
    """
    _redirect_stdout()
    from conclave.persistence import MeetingPersistence
    persistence = MeetingPersistence(data_dir)

    if meeting_id:
        result = persistence.load(meeting_id)
        if not result:
            return f"Meeting '{meeting_id}' not found."
        return f"# {meeting_id}\n{_format_server_result(result)}"

    meetings = persistence.list_meetings(search=search)
    if not meetings:
        return "No meetings in history."
    lines = ["| Meeting ID | Goal | Completed | Topic |", "| --- | --- | --- | --- |"]
    for m in meetings:
        lines.append(
            f"| {m.get('meeting_id','?')} | {m.get('goal','?')} "
            f"| {m.get('completed_at','?')[:19]} | {m.get('topic','')} |"
        )
    return "\n".join(lines)


_active_servers: dict[int, object] = {}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
