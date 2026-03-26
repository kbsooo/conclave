"""CLI entrypoint — conclave run | serve | join."""

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
from conclave.models import AgentConfig
from conclave.orchestrator import MeetingOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="conclave",
        description="Multi-agent meeting system",
    )
    subparsers = parser.add_subparsers(dest="command")

    # conclave run  (v0.1 — single machine)
    run_parser = subparsers.add_parser("run", help="Run a meeting locally (v0.1)")
    run_parser.add_argument("config", help="Path to meeting config (YAML/JSON)")
    run_parser.add_argument("--brief", action="store_true", help="Run pre-meeting briefing")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    # conclave serve  (v0.2 — host a meeting room)
    serve_parser = subparsers.add_parser("serve", help="Host a meeting room (v0.2)")
    serve_parser.add_argument("config", help="Path to meeting config (YAML/JSON)")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    serve_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    # conclave join  (v0.2 — join a remote meeting)
    join_parser = subparsers.add_parser("join", help="Join a remote meeting (v0.2)")
    join_parser.add_argument("server", help="Server URL (e.g., http://localhost:8080)")
    join_parser.add_argument("agent", help="Path to agent config (YAML/JSON)")
    join_parser.add_argument("--brief", action="store_true", help="Run pre-meeting briefing before joining")
    join_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )

    if args.command == "run":
        asyncio.run(_run_meeting(args.config, args.brief))
    elif args.command == "serve":
        asyncio.run(_serve_meeting(args.config, args.host, args.port))
    elif args.command == "join":
        asyncio.run(_join_meeting(args.server, args.agent, args.brief))


# ── v0.1: local meeting ────────────────────────────────────────────


async def _run_meeting(config_path: str, use_briefing: bool) -> None:
    config = load_meeting_config(config_path)

    if use_briefing:
        config = await brief_all_agents(config)

    result = await MeetingOrchestrator(config).run()
    _print_meeting_result(result)


# ── v0.2: server ───────────────────────────────────────────────────


async def _serve_meeting(config_path: str, host: str, port: int) -> None:
    from conclave.server import serve

    config = load_meeting_config(config_path)
    result_data = await serve(config, host=host, port=port)

    if result_data:
        _print_server_result(result_data)


# ── v0.2: client ───────────────────────────────────────────────────


async def _join_meeting(server_url: str, agent_path: str, use_briefing: bool) -> None:
    from conclave.client import MeetingClient

    agent_config = _load_agent_config(agent_path)

    if use_briefing:
        # Fetch meeting info for briefing context
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{server_url.rstrip('/')}/meeting") as resp:
                meeting_info = await resp.json()

        from conclave.briefing import brief_agent
        from conclave.models import MeetingConfig

        # Create a minimal meeting config for the briefing
        temp_config = MeetingConfig(
            meeting_id=meeting_info["meeting_id"],
            topic=meeting_info["topic"],
            context=meeting_info.get("context", ""),
        )
        enriched = await brief_agent(agent_config, temp_config)
        agent_config = agent_config.model_copy(update={"instruction": enriched})

    client = MeetingClient(server_url, agent_config)
    result_data = await client.run()

    if result_data:
        _print_server_result(result_data)


# ── Agent config loading ───────────────────────────────────────────


def _load_agent_config(path: str) -> AgentConfig:
    """Load a single AgentConfig from YAML/JSON."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8")

    if p.suffix in (".yaml", ".yml"):
        parsed = yaml.safe_load(raw)
    elif p.suffix == ".json":
        parsed = json.loads(raw)
    else:
        parsed = yaml.safe_load(raw)

    return AgentConfig.model_validate(parsed)


# ── Output display ─────────────────────────────────────────────────


def _print_meeting_result(result) -> None:
    """Display a v0.1 MeetingResult object."""
    print(f"\n{'=' * 60}")
    print("MEETING MINUTES")
    print(f"{'=' * 60}")
    print(result.minutes.summary)

    if result.minutes.key_points:
        print("\nKey Points:")
        for point in result.minutes.key_points:
            print(f"  - {point}")

    if result.minutes.decisions:
        print("\nDecisions:")
        for decision in result.minutes.decisions:
            print(f"  - {decision}")

    if result.artifact:
        print(f"\n{'=' * 60}")
        print(f"ARTIFACT ({result.artifact.goal.value})")
        print(f"{'=' * 60}")
        print(result.artifact.content)

    if result.personal_reports:
        for owner_id, report in result.personal_reports.items():
            print(f"\n{'=' * 60}")
            print(f"PERSONAL REPORT — {owner_id}")
            print(f"{'=' * 60}")
            print(report.summary)

    print(f"\n[Ended: {result.termination_reason}]")


def _print_server_result(data: dict) -> None:
    """Display a v0.2 result dict from the server."""
    print(f"\n{'=' * 60}")
    print("MEETING MINUTES")
    print(f"{'=' * 60}")
    print(data.get("minutes_raw", "(no minutes)"))

    artifact = data.get("artifact_raw")
    if artifact:
        goal = data.get("artifact_goal", "unknown")
        print(f"\n{'=' * 60}")
        print(f"ARTIFACT ({goal})")
        print(f"{'=' * 60}")
        print(artifact)

    reports = data.get("personal_reports", {})
    for agent_id, report_info in reports.items():
        owner_id = report_info.get("owner_id", agent_id)
        print(f"\n{'=' * 60}")
        print(f"PERSONAL REPORT — {owner_id}")
        print(f"{'=' * 60}")
        print(report_info.get("content", "(no report)"))

    print(f"\n[Ended: {data.get('termination_reason', 'Unknown')}]")


if __name__ == "__main__":
    main()
