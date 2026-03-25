"""Thin litellm wrapper — the only module that touches LLM APIs directly."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import litellm

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMClient:
    """Async LLM gateway with token tracking.

    All LLM calls in the codebase go through this class.
    Never import litellm directly from other modules.
    """
    total_prompt_tokens: int = field(default=0, init=False)
    total_completion_tokens: int = field(default=0, init=False)

    async def complete(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Single LLM completion call with automatic token tracking."""
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        usage = response.usage
        result = LLMResponse(
            content=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

        self.total_prompt_tokens += result.prompt_tokens
        self.total_completion_tokens += result.completion_tokens

        logger.debug(
            "LLM call: model=%s, tokens=%d (prompt=%d, completion=%d)",
            model, result.total_tokens, result.prompt_tokens, result.completion_tokens,
        )
        return result

    async def complete_json(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """LLM completion with JSON response format."""
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

        usage = response.usage
        result = LLMResponse(
            content=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

        self.total_prompt_tokens += result.prompt_tokens
        self.total_completion_tokens += result.completion_tokens
        return result

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens
