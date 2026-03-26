"""Meeting configuration loading and validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from conclave.models import MeetingConfig

logger = logging.getLogger(__name__)


def load_meeting_config(
    path: str | Path | None = None,
    data: dict | None = None,
) -> MeetingConfig:
    """Load and validate meeting config from YAML/JSON file or dict.

    If context_files are specified, their contents are loaded and
    appended to the context field. File paths are resolved relative
    to the config file's directory.
    """
    if data is not None:
        config = MeetingConfig.model_validate(data)
        return _load_context_files(config, base_dir=Path.cwd())

    if path is None:
        raise ValueError("Either 'path' or 'data' must be provided")

    path = Path(path)
    raw = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        parsed = yaml.safe_load(raw)
    elif path.suffix == ".json":
        parsed = json.loads(raw)
    else:
        parsed = yaml.safe_load(raw)

    config = MeetingConfig.model_validate(parsed)
    return _load_context_files(config, base_dir=path.parent.resolve())


def _load_context_files(config: MeetingConfig, base_dir: Path) -> MeetingConfig:
    """Load context_files and append their contents to config.context."""
    if not config.context_files:
        return config

    file_sections: list[str] = []

    for file_path_str in config.context_files:
        file_path = base_dir / file_path_str
        if not file_path.exists():
            logger.warning("Context file not found, skipping: %s", file_path)
            continue

        content = file_path.read_text(encoding="utf-8")
        file_sections.append(f"--- {file_path.name} ---\n{content}")
        logger.info("Loaded context file: %s (%d chars)", file_path.name, len(content))

    if not file_sections:
        return config

    # Append file contents to existing context
    merged_context = config.context
    if merged_context:
        merged_context += "\n\n"
    merged_context += "\n\n".join(file_sections)

    return config.model_copy(update={"context": merged_context})
