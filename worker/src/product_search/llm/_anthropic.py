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
    temperature: float | None = None,
    cache_system: bool = False,
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

    # ``omit`` is the SDK sentinel meaning "leave at the provider default".
    temperature_arg: float | anthropic.Omit = (
        temperature if temperature is not None else anthropic.omit
    )

    # ADR-142: when caching is requested, send ``system`` as a content-block
    # list carrying ``cache_control: ephemeral`` so the (stable) system prompt
    # is cached for ~5 min and re-sent at the cache-read rate. Otherwise send
    # the bare string (unchanged behaviour for every other caller).
    system_arg: str | list[anthropic.types.TextBlockParam]
    if cache_system:
        system_arg = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_arg = system

    try:
        resp = client.messages.create(
            model=model,
            system=system_arg,
            messages=sdk_messages,
            max_tokens=max_tokens,
            temperature=temperature_arg,
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

    # Cache token counts are present on the usage object only when caching was
    # exercised; ``getattr`` keeps this resilient across SDK versions.
    usage = resp.usage
    return LLMResponse(
        provider=provider,
        model=model,
        text=text,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cache_read_input_tokens=(
            getattr(usage, "cache_read_input_tokens", None) if usage else None
        ),
        cache_creation_input_tokens=(
            getattr(usage, "cache_creation_input_tokens", None) if usage else None
        ),
    )
