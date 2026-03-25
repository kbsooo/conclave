"""Meeting configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from conclave.models import MeetingConfig


def load_meeting_config(
    path: str | Path | None = None,
    data: dict | None = None,
) -> MeetingConfig:
    """Load and validate meeting config from YAML/JSON file or dict.

    Pydantic validation catches misconfiguration before any LLM call.
    """
    if data is not None:
        return MeetingConfig.model_validate(data)

    if path is None:
        raise ValueError("Either 'path' or 'data' must be provided")

    path = Path(path)
    raw = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        parsed = yaml.safe_load(raw)
    elif path.suffix == ".json":
        parsed = json.loads(raw)
    else:
        # Try YAML first (superset of JSON)
        parsed = yaml.safe_load(raw)

    return MeetingConfig.model_validate(parsed)
