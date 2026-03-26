"""CLI entrypoint — conclave run | serve | join | create | list | status | history | mcp."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import yaml

from conclave.briefing import brief_all_agents
from conclave.config import load_meeting_config
from conclave.models import AgentConfig, ServerConfig
from conclave.orchestrator import MeetingOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(prog="conclave", description="Multi-agent meeting system")
    subparsers = parser.add_subparsers(dest="command")

    # conclave run  (v0.1)
    p = subparsers.add_parser("run", help="Run a meeting locally (v0.1)")
    p.add_argument("config", help="Path to meeting config (YAML/JSON)")
    p.add_argument("--brief", action="store_true", help="Run pre-meeting briefing")
    p.add_argument("--verbose", "-v", action="store_true")

    # conclave serve  (v0.4)
    p = subparsers.add_parser("serve", help="Host a meeting room server")
    p.add_argument("config", nargs="?", help="Path to initial meeting config (optional)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--api-key", action="append", dest="api_keys", default=[])
    p.add_argument("--no-auth", action="store_true", help="Disable authentication")
    p.add_argument("--data-dir", default="~/.conclave")
    p.add_argument("--verbose", "-v", action="store_true")

    # conclave join  (v0.4)
    p = subparsers.add_parser("join", help="Join a remote meeting")
    p.add_argument("server", help="Server URL (e.g., http://localhost:8080)")
    p.add_argument("agent", help="Path to agent config (YAML/JSON)")
    p.add_argument("--meeting-id", default="", help="Meeting to join (default: first pending)")
    p.add_argument("--api-key", default="", help="Server API key")
    p.add_argument("--brief", action="store_true", help="Run pre-meeting briefing")
    p.add_argument("--verbose", "-v", action="store_true")

    # conclave create  (v0.4)
    p = subparsers.add_parser("create", help="Create a meeting on a remote server")
    p.add_argument("server", help="Server URL")
    p.add_argument("config", help="Path to meeting config (YAML/JSON)")
    p.add_argument("--api-key", default="")
    p.add_argument("--verbose", "-v", action="store_true")

    # conclave list  (v0.4)
    p = subparsers.add_parser("list", help="List meetings on a server")
    p.add_argument("server", help="Server URL")
    p.add_argument("--status", default="", help="Filter by status (pending/in_progress/completed)")
    p.add_argument("--search", default="", help="Search topic")
    p.add_argument("--api-key", default="")

    # conclave status  (v0.4)
    p = subparsers.add_parser("status", help="Get meeting status")
    p.add_argument("server", help="Server URL")
    p.add_argument("meeting_id", help="Meeting ID")
    p.add_argument("--api-key", default="")

    # conclave history  (v0.4)
    p = subparsers.add_parser("history", help="Query past meeting results (local)")
    p.add_argument("--meeting-id", default="", help="Specific meeting to view")
    p.add_argument("--search", default="", help="Search by topic")
    p.add_argument("--data-dir", default="~/.conclave")

    # conclave mcp
    subparsers.add_parser("mcp", help="Run as MCP server (stdio transport)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "mcp":
        from conclave.mcp_server import main as mcp_main
        mcp_main()
        return

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    handler = {
        "run": lambda: asyncio.run(_cmd_run(args)),
        "serve": lambda: asyncio.run(_cmd_serve(args)),
        "join": lambda: asyncio.run(_cmd_join(args)),
        "create": lambda: asyncio.run(_cmd_create(args)),
        "list": lambda: asyncio.run(_cmd_list(args)),
        "status": lambda: asyncio.run(_cmd_status(args)),
        "history": lambda: _cmd_history(args),
    }.get(args.command)

    if handler:
        handler()


# ── Command implementations ────────────────────────────────────────


async def _cmd_run(args) -> None:
    config = load_meeting_config(args.config)
    if args.brief:
        config = await brief_all_agents(config)
    result = await MeetingOrchestrator(config).run()
    _print_meeting_result(result)


async def _cmd_serve(args) -> None:
    from conclave.server import serve

    initial_meeting = None
    if args.config:
        initial_meeting = load_meeting_config(args.config)

    api_keys = args.api_keys if not args.no_auth else [""]
    sc = ServerConfig(api_keys=api_keys, data_dir=args.data_dir)

    await serve(
        host=args.host, port=args.port,
        server_config=sc, initial_meeting=initial_meeting,
    )


async def _cmd_join(args) -> None:
    from conclave.client import MeetingClient

    agent_config = _load_agent_config(args.agent)
    api_key = args.api_key

    if args.brief:
        import aiohttp
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        meeting_id = args.meeting_id
        async with aiohttp.ClientSession(headers=headers) as session:
            # Get meeting info for briefing
            if meeting_id:
                url = f"{args.server.rstrip('/')}/meetings/{meeting_id}"
            else:
                url = f"{args.server.rstrip('/')}/meetings?status=pending"
            async with session.get(url) as resp:
                data = await resp.json()
                if isinstance(data, list):
                    if not data:
                        print("No pending meetings found.")
                        return
                    data = data[0]
                    meeting_id = data["meeting_id"]

        from conclave.briefing import brief_agent
        from conclave.models import MeetingConfig
        temp_config = MeetingConfig(
            meeting_id=data.get("meeting_id", ""),
            topic=data.get("topic", ""),
            context=data.get("context", ""),
        )
        enriched = await brief_agent(agent_config, temp_config)
        agent_config = agent_config.model_copy(update={"instruction": enriched})

    client = MeetingClient(
        args.server, agent_config,
        meeting_id=args.meeting_id, api_key=api_key,
    )
    result = await client.run()
    if result:
        _print_server_result(result)


async def _cmd_create(args) -> None:
    import aiohttp
    config = load_meeting_config(args.config)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            f"{args.server.rstrip('/')}/meetings",
            json=config.model_dump(mode="json"),
        ) as resp:
            data = await resp.json()
            if resp.status == 201:
                print(f"Created meeting: {data['meeting_id']}")
                print(f"  Topic: {data['topic']}")
                print(f"  Goal: {data['goal']}")
                print(f"  Expected agents: {data['expected_agents']}")
            else:
                print(f"Error: {data.get('error', 'unknown')}")


async def _cmd_list(args) -> None:
    import aiohttp
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    params = {}
    if args.status:
        params["status"] = args.status
    if args.search:
        params["search"] = args.search

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(
            f"{args.server.rstrip('/')}/meetings", params=params,
        ) as resp:
            meetings = await resp.json()

    if not meetings:
        print("No meetings found.")
        return

    for m in meetings:
        agents = m.get("agents", [])
        print(f"  {m['meeting_id']:30s}  {m['status']:12s}  {len(agents)}/{m.get('expected_agents', '?')} agents  {m['topic']}")


async def _cmd_status(args) -> None:
    import aiohttp
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(
            f"{args.server.rstrip('/')}/meetings/{args.meeting_id}",
        ) as resp:
            data = await resp.json()

    if "error" in data:
        print(f"Error: {data['error']}")
        return

    print(f"Meeting: {data['meeting_id']}")
    print(f"  Topic: {data['topic']}")
    print(f"  Goal: {data['goal']}")
    print(f"  Status: {data['status']}")
    print(f"  Agents: {', '.join(data.get('agents', []))}")
    print(f"  Expected: {data.get('expected_agents', '?')}")


def _cmd_history(args) -> None:
    from conclave.persistence import MeetingPersistence
    persistence = MeetingPersistence(args.data_dir)

    if args.meeting_id:
        result = persistence.load(args.meeting_id)
        if result:
            _print_server_result(result)
        else:
            print(f"Meeting '{args.meeting_id}' not found in history.")
        return

    meetings = persistence.list_meetings(search=args.search)
    if not meetings:
        print("No meetings in history.")
        return

    for m in meetings:
        print(f"  {m.get('meeting_id', '?'):30s}  {m.get('goal', '?'):12s}  {m.get('completed_at', '?')[:19]}  {m.get('topic', '')}")


# ── Helpers ─────────────────────────────────────────────────────────


def _load_agent_config(path: str) -> AgentConfig:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        parsed = yaml.safe_load(raw)
    elif p.suffix == ".json":
        parsed = json.loads(raw)
    else:
        parsed = yaml.safe_load(raw)
    return AgentConfig.model_validate(parsed)


def _print_meeting_result(result) -> None:
    print(f"\n{'=' * 60}")
    print("MEETING MINUTES")
    print(f"{'=' * 60}")
    print(result.minutes.summary)
    if result.minutes.key_points:
        print("\nKey Points:")
        for p in result.minutes.key_points:
            print(f"  - {p}")
    if result.minutes.decisions:
        print("\nDecisions:")
        for d in result.minutes.decisions:
            print(f"  - {d}")
    if result.artifact:
        print(f"\n{'=' * 60}")
        print(f"ARTIFACT ({result.artifact.goal.value})")
        print(f"{'=' * 60}")
        print(result.artifact.content)
    for owner_id, report in result.personal_reports.items():
        print(f"\n{'=' * 60}")
        print(f"PERSONAL REPORT — {owner_id}")
        print(f"{'=' * 60}")
        print(report.summary)
    print(f"\n[Ended: {result.termination_reason}]")


def _print_server_result(data: dict) -> None:
    print(f"\n{'=' * 60}")
    print("MEETING MINUTES")
    print(f"{'=' * 60}")
    print(data.get("minutes_raw", "(no minutes)"))
    artifact = data.get("artifact_raw")
    if artifact:
        print(f"\n{'=' * 60}")
        print(f"ARTIFACT ({data.get('artifact_goal', 'unknown')})")
        print(f"{'=' * 60}")
        print(artifact)
    for agent_id, report_info in data.get("personal_reports", {}).items():
        owner_id = report_info.get("owner_id", agent_id)
        print(f"\n{'=' * 60}")
        print(f"PERSONAL REPORT — {owner_id}")
        print(f"{'=' * 60}")
        print(report_info.get("content", "(no report)"))
    print(f"\n[Ended: {data.get('termination_reason', 'Unknown')}]")


if __name__ == "__main__":
    main()
