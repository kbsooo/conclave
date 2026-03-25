"""Agent backends — how an agent actually generates responses.

Two backends:
- CLIBackend: runs a CLI agent (claude, openclaw, codex, etc.)
  The CLI agent already has memory about the user — lightweight instruction only.
- LiteLLMBackend: raw API calls via litellm (no memory, needs full persona).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Protocol

from conclave.llm import LLMClient

logger = logging.getLogger(__name__)


class Backend(Protocol):
    """Interface for agent response generation.

    Any backend just needs to: take a prompt, return a string.
    """

    async def generate(self, prompt: str) -> str:
        """Send prompt, get response."""
        ...


class CLIBackend:
    """Runs a CLI-based agent (claude, openclaw, codex, etc.).

    The CLI agent already knows the user via its built-in memory system.
    Conclave sends the meeting context + transcript as a prompt,
    and the CLI agent responds using its existing knowledge of the user.

    This is the primary backend — it produces the most authentic representation
    because the agent has real context about the person.
    """

    def __init__(self, command: str = "claude", args: list[str] | None = None, timeout: int = 120) -> None:
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self._validate_command()

    def _validate_command(self) -> None:
        if shutil.which(self.command) is None:
            raise FileNotFoundError(
                f"CLI agent '{self.command}' not found in PATH. "
                f"Install it or provide the full path."
            )

    async def generate(self, prompt: str) -> str:
        """Run the CLI agent with the prompt and capture its output."""
        cmd = [self.command] + self._build_args() + [prompt]

        logger.debug("CLI call: %s (prompt length: %d chars)", self.command, len(prompt))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            raise TimeoutError(
                f"CLI agent '{self.command}' timed out after {self.timeout}s"
            )

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RuntimeError(
                f"CLI agent '{self.command}' failed (exit {process.returncode}): {error_msg}"
            )

        return stdout.decode().strip()

    def _build_args(self) -> list[str]:
        """Build CLI arguments based on the command type."""
        if self.args:
            return self.args

        # Default args for known CLI agents
        known_defaults: dict[str, list[str]] = {
            "claude": ["-p", "--output-format", "text"],
            "codex": ["-q"],
        }
        return known_defaults.get(self.command, [])


class LiteLLMBackend:
    """API-based backend via litellm. No memory — needs full persona in prompt.

    Supports: OpenAI, Anthropic, Ollama, Bedrock, Vertex, Azure, etc.
    Use this when CLI agents are not available, or for quick prototyping.
    """

    def __init__(
        self,
        model: str = "openai/gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        llm: LLMClient | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.llm = llm or LLMClient()

    async def generate(self, prompt: str) -> str:
        """Send prompt as a single user message to the API."""
        response = await self.llm.complete(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.content


def create_backend(
    backend_type: str = "cli",
    command: str = "claude",
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.7,
    llm: LLMClient | None = None,
    cli_args: list[str] | None = None,
    cli_timeout: int = 120,
) -> Backend:
    """Factory for creating backends from config values."""
    if backend_type == "cli":
        return CLIBackend(command=command, args=cli_args, timeout=cli_timeout)
    elif backend_type == "api":
        return LiteLLMBackend(model=model, temperature=temperature, llm=llm)
    else:
        raise ValueError(f"Unknown backend type: {backend_type!r}. Use 'cli' or 'api'.")
