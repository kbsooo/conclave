"""Meeting result persistence — save and query completed meetings.

Results are stored as JSON files:
    ~/.conclave/meetings/<meeting_id>/
        meta.json         — lightweight index (topic, goal, status, timestamps)
        result.json       — full meeting result
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class MeetingPersistence:
    """Save and load meeting results from disk."""

    def __init__(self, data_dir: str = "~/.conclave") -> None:
        self.base_dir = Path(data_dir).expanduser() / "meetings"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, meeting_id: str, result_data: dict) -> Path:
        """Persist a completed meeting's result. Returns the directory path."""
        meeting_dir = self.base_dir / self._safe_name(meeting_id)
        meeting_dir.mkdir(parents=True, exist_ok=True)

        # Full result
        result_path = meeting_dir / "result.json"
        result_path.write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # Lightweight meta for fast listing
        meta = {
            "meeting_id": meeting_id,
            "topic": result_data.get("topic", ""),
            "goal": result_data.get("artifact_goal", ""),
            "status": result_data.get("status", "completed"),
            "termination_reason": result_data.get("termination_reason", ""),
            "agent_count": len(result_data.get("personal_reports", {})),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = meeting_dir / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info("Saved meeting '%s' to %s", meeting_id, meeting_dir)
        return meeting_dir

    def load(self, meeting_id: str) -> dict | None:
        """Load a meeting's full result. Returns None if not found."""
        result_path = self.base_dir / self._safe_name(meeting_id) / "result.json"
        if not result_path.exists():
            return None
        return json.loads(result_path.read_text(encoding="utf-8"))

    def list_meetings(
        self, limit: int = 50, offset: int = 0, search: str = "",
    ) -> list[dict]:
        """List persisted meetings by reading meta.json files.

        Sorted by completion time (newest first).
        """
        metas: list[dict] = []

        for meta_path in self.base_dir.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if search and search.lower() not in meta.get("topic", "").lower():
                    continue
                metas.append(meta)
            except (json.JSONDecodeError, OSError):
                continue

        # Sort newest first
        metas.sort(key=lambda m: m.get("completed_at", ""), reverse=True)
        return metas[offset : offset + limit]

    def exists(self, meeting_id: str) -> bool:
        return (self.base_dir / self._safe_name(meeting_id) / "result.json").exists()

    @staticmethod
    def _safe_name(meeting_id: str) -> str:
        """Sanitize meeting_id for use as a directory name."""
        return meeting_id.replace("/", "_").replace("\\", "_").replace("..", "_")
