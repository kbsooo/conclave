"""Conclave MCP server — expose meetings as tools for AI agents.

Agents can run meetings, host meeting rooms, or join existing ones
via MCP tool calls. No CLI wrapping required.

Usage (stdio, for Claude Code):
    conclave mcp
    # or: python -m conclave.mcp_server

Configure in Claude Code settings:
    {
        "mcpServers": {
            "conclave": {
                "command": "conclave",
                "args": ["mcp"]
            }
        }
    }
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
    """Redirect stdout → stderr so prints don't corrupt MCP stdio transport."""
    sys.stdout = sys.stderr


# ── Tools ───────────────────────────────────────────────────────────


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

    Each perspective becomes an agent that discusses the topic autonomously.
    Returns minutes, artifact (based on goal), and per-agent reports.

    Args:
        topic: The meeting topic or question to discuss.
        perspectives: JSON array of perspectives. Example:
            [{"name": "optimist", "instruction": "focus on opportunities"},
             {"name": "pragmatist", "instruction": "focus on feasibility"},
             {"name": "critic", "instruction": "find risks and weaknesses"}]
        context: Additional background information shared with all agents.
        goal: What the meeting produces — "brainstorm" (ranked ideas),
            "code" (working code), "document" (polished doc), "decision" (decision record).
        max_rounds: Maximum discussion rounds before forced termination.
        command: CLI agent command — "claude", "gemini", "codex", "openclaw".
    """
    _redirect_stdout()

    from conclave.models import (
        AgentConfig,
        MeetingConfig,
        MeetingGoal,
        TerminationMode,
    )
    from conclave.orchestrator import MeetingOrchestrator

    # Parse perspectives
    try:
        persp_list = json.loads(perspectives)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON in perspectives — {e}"

    if not persp_list:
        return "Error: At least one perspective is required."

    # Validate goal
    try:
        meeting_goal = MeetingGoal(goal)
    except ValueError:
        return f"Error: Invalid goal '{goal}'. Use: brainstorm, code, document, decision"

    # Build agents
    agents = [
        AgentConfig(
            agent_id=p.get("name", f"agent-{i}"),
            owner_id="mcp-user",
            backend="cli",
            command=command,
            instruction=p.get("instruction", ""),
        )
        for i, p in enumerate(persp_list)
    ]

    config = MeetingConfig(
        meeting_id=f"mcp-{topic[:30].replace(' ', '-').lower()}",
        topic=topic,
        context=context,
        goal=meeting_goal,
        agents=agents,
        termination=TerminationMode.SUPERMAJORITY_VOTE,
        max_rounds=max_rounds,
    )

    # Run the sealed meeting
    result = await MeetingOrchestrator(config).run()

    # Format result
    out: list[str] = []
    out.append(f"# Meeting: {topic}")
    out.append(f"**Goal:** {goal} | **Ended:** {result.termination_reason}\n")

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

    if result.minutes.action_items:
        out.append("\n### Action Items")
        for a in result.minutes.action_items:
            out.append(f"- {a}")

    if result.artifact:
        out.append(f"\n## Artifact ({result.artifact.goal.value})")
        out.append(result.artifact.content)

    for owner_id, report in result.personal_reports.items():
        out.append(f"\n## Report — {report.agent_id}")
        out.append(report.summary)

    return "\n".join(out)


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

    Starts an HTTP server. The meeting auto-starts when the expected
    number of agents have joined. Other agents join via conclave_join
    or `conclave join <url> agent.yaml`.

    Args:
        topic: The meeting topic.
        context: Shared background information.
        goal: "brainstorm", "code", "document", or "decision".
        expected_agents: Auto-seal after this many agents join.
        port: Server port to listen on.
        max_rounds: Maximum discussion rounds.
    """
    _redirect_stdout()

    from aiohttp import web

    from conclave.models import (
        MeetingConfig,
        MeetingGoal,
        TerminationMode,
    )
    from conclave.server import create_app

    try:
        meeting_goal = MeetingGoal(goal)
    except ValueError:
        return f"Error: Invalid goal '{goal}'. Use: brainstorm, code, document, decision"

    config = MeetingConfig(
        meeting_id=f"mcp-hosted-{topic[:20].replace(' ', '-').lower()}",
        topic=topic,
        context=context,
        goal=meeting_goal,
        termination=TerminationMode.SUPERMAJORITY_VOTE,
        max_rounds=max_rounds,
        expected_agents=expected_agents,
    )

    app = create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    _active_servers[port] = runner

    url = f"http://localhost:{port}"
    return (
        f"Meeting room open at {url}\n"
        f"Topic: {topic}\n"
        f"Goal: {goal}\n"
        f"Waiting for {expected_agents} agents...\n\n"
        f"Join with:\n"
        f"  conclave join {url} agent.yaml\n"
        f"  conclave_join(server_url=\"{url}\", agent_id=\"my-agent\", instruction=\"...\")"
    )


@mcp.tool()
async def conclave_join(
    server_url: str,
    agent_id: str,
    instruction: str = "",
    owner_id: str = "mcp-user",
    command: str = "claude",
) -> str:
    """Join a remote meeting room and participate until it ends.

    The agent speaks, votes, and generates reports autonomously using
    the configured CLI agent. Blocks until the meeting completes.

    Args:
        server_url: Meeting room URL (e.g., http://localhost:8080).
        agent_id: Unique name for this agent in the meeting.
        instruction: Private guidance (never shared with other agents).
        owner_id: Who this agent represents.
        command: CLI agent — "claude", "gemini", "codex", "openclaw".
    """
    _redirect_stdout()

    from conclave.client import MeetingClient
    from conclave.models import AgentConfig

    agent_config = AgentConfig(
        agent_id=agent_id,
        owner_id=owner_id,
        backend="cli",
        command=command,
        instruction=instruction,
    )

    client = MeetingClient(server_url, agent_config)
    result = await client.run()

    if not result:
        return "Meeting ended but no results were returned."

    out: list[str] = []
    out.append(f"# Meeting Results")
    out.append(f"**Ended:** {result.get('termination_reason', 'Unknown')}\n")

    minutes = result.get("minutes_raw")
    if minutes:
        out.append("## Minutes")
        out.append(minutes)

    artifact = result.get("artifact_raw")
    if artifact:
        out.append(f"\n## Artifact ({result.get('artifact_goal', 'unknown')})")
        out.append(artifact)

    my_report = result.get("personal_reports", {}).get(agent_id)
    if my_report:
        out.append("\n## Your Personal Report")
        out.append(my_report.get("content", ""))

    return "\n".join(out)


# Active server runners (for potential cleanup)
_active_servers: dict[int, object] = {}


def main() -> None:
    """Run the Conclave MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
