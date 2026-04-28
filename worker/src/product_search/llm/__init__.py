"""LLM provider abstraction.

Public surface::

    from product_search.llm import call_llm, LLMResponse, Message

    resp = call_llm(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="You are a helpful assistant.",
        messages=[Message(role="user", content="Say hello.")],
    )
    print(resp.text)

Provider-specific details live in the per-provider modules:
  - ``_anthropic.py``  — Anthropic SDK
  - ``_openai.py``     — OpenAI SDK (also used for GLM via base-URL override)
  - ``_gemini.py``     — Google GenAI SDK

All providers raise ``LLMError`` on failure so callers don't need to know
provider-specific exception types.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ProviderName = Literal["anthropic", "openai", "gemini", "glm"]

_CallFn = Callable[..., "LLMResponse"]


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    provider: str
    model: str
    text: str
    input_tokens: int | None
    output_tokens: int | None


class LLMError(RuntimeError):
    """Raised when an LLM call fails (auth, quota, timeout, etc.)."""


def call_llm(
    *,
    provider: ProviderName,
    model: str,
    system: str,
    messages: list[Message],
    response_format: Literal["text", "json"] = "text",
    max_tokens: int = 2048,
) -> LLMResponse:
    """Call the specified LLM provider.

    Args:
        provider: One of ``"anthropic"``, ``"openai"``, ``"gemini"``, ``"glm"``.
        model: Model identifier as the provider understands it.
        system: System prompt string.
        messages: Conversation turns (role + content).
        response_format: ``"text"`` or ``"json"``; provider support varies.
        max_tokens: Maximum output tokens.

    Returns:
        ``LLMResponse`` with the text reply and token-usage info.

    Raises:
        ``LLMError`` on any failure.
        ``ImportError`` if the required SDK package isn't installed.
    """
    _call: _CallFn
    if provider == "anthropic":
        from product_search.llm._anthropic import call as _call
    elif provider in ("openai", "glm"):
        from product_search.llm._openai import call as _call
    elif provider == "gemini":
        from product_search.llm._gemini import call as _call
    else:
        raise LLMError(f"Unknown provider: {provider!r}")

    return _call(
        provider=provider,
        model=model,
        system=system,
        messages=messages,
        response_format=response_format,
        max_tokens=max_tokens,
    )
