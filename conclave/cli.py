"""CLI entrypoint — `conclave run meeting.yaml`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from conclave.briefing import brief_all_agents
from conclave.config import load_meeting_config
from conclave.orchestrator import MeetingOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="conclave",
        description="Multi-agent meeting system",
    )
    subparsers = parser.add_subparsers(dest="command")

    # conclave run
    run_parser = subparsers.add_parser("run", help="Run a meeting from config")
    run_parser.add_argument("config", help="Path to meeting config (YAML/JSON)")
    run_parser.add_argument("--brief", action="store_true", help="Run pre-meeting briefing")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)s %(name)s %(message)s",
        )
        asyncio.run(_run_meeting(args.config, args.brief))


async def _run_meeting(config_path: str, use_briefing: bool) -> None:
    config = load_meeting_config(config_path)

    if use_briefing:
        config = await brief_all_agents(config)

    result = await MeetingOrchestrator(config).run()

    # ── Output ─────────────────────────────────────────────────────
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


if __name__ == "__main__":
    main()
