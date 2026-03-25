"""Tests for backend abstraction."""

import pytest

from conclave.backend import CLIBackend, LiteLLMBackend, create_backend


def test_create_cli_backend():
    # 'echo' exists on all unix systems
    backend = create_backend(backend_type="cli", command="echo")
    assert isinstance(backend, CLIBackend)


def test_create_api_backend():
    backend = create_backend(backend_type="api", model="openai/gpt-4o-mini")
    assert isinstance(backend, LiteLLMBackend)


def test_create_unknown_backend():
    with pytest.raises(ValueError, match="Unknown backend type"):
        create_backend(backend_type="unknown")


def test_cli_backend_missing_command():
    with pytest.raises(FileNotFoundError, match="not found in PATH"):
        CLIBackend(command="nonexistent_binary_xyz_123")


@pytest.mark.asyncio
async def test_cli_backend_echo():
    """Smoke test: echo backend should return the prompt."""
    backend = CLIBackend(command="echo", args=[])
    result = await backend.generate("hello world")
    assert "hello world" in result
