"""Anthropic provider for the LLM abstraction.

Requires: ``anthropic>=0.30`` (not yet in pyproject.toml — added in Phase 2
when the synthesizer needs it).  This module is imported lazily, so the worker
package can be imported even if the SDK isn't installed.
"""

from __future__ import annotations

from typing import Literal

from product_search.llm import LLMError, LLMResponse, Message


def call(
    *,
    provider: str,
    model: str,
    system: str,
    messages: list[Message],
    response_format: Literal["text", "json"] = "text",
    max_tokens: int = 2048,
) -> LLMResponse:
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "anthropic SDK not installed. Run: pip install 'anthropic>=0.30'"
        ) from exc

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    sdk_messages: list[anthropic.types.MessageParam] = [
        {"role": m.role, "content": m.content} for m in messages
    ]

    try:
        resp = client.messages.create(
            model=model,
            system=system,
            messages=sdk_messages,
            max_tokens=max_tokens,
        )
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError(f"Anthropic connection error: {exc}") from exc

    # Extract text from the first TextBlock; other block types don't have .text.
    text = ""
    for block in resp.content:
        if hasattr(block, "text") and isinstance(block.text, str):
            text = block.text
            break

    return LLMResponse(
        provider=provider,
        model=model,
        text=text,
        input_tokens=resp.usage.input_tokens if resp.usage else None,
        output_tokens=resp.usage.output_tokens if resp.usage else None,
    )
