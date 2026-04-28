"""Google Gemini provider for the LLM abstraction.

The google-generativeai SDK has limited type stubs; we suppress errors on
the SDK surface with ``# type: ignore[import-untyped]`` and targeted ignores.

Requires: ``google-generativeai>=0.7`` (added in Phase 2).
"""

from __future__ import annotations

import os
from typing import Any, Literal

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
        import google.generativeai as genai  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "google-generativeai SDK not installed. "
            "Run: pip install 'google-generativeai>=0.7'"
        ) from exc

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY environment variable not set.")

    genai.configure(api_key=api_key)  # type: ignore[attr-defined]

    generation_config: dict[str, Any] = {"max_output_tokens": max_tokens}
    if response_format == "json":
        generation_config["response_mime_type"] = "application/json"

    client = genai.GenerativeModel(  # type: ignore[attr-defined]
        model_name=model,
        system_instruction=system,
        generation_config=generation_config,  # type: ignore[arg-type]
    )

    # Build Gemini-format history + final user turn.
    history: list[dict[str, Any]] = []
    for m in messages[:-1]:
        history.append({"role": m.role, "parts": [m.content]})

    last_msg = messages[-1].content if messages else ""

    try:
        chat = client.start_chat(history=history)  # type: ignore[arg-type]
        resp = chat.send_message(last_msg)
    except Exception as exc:
        raise LLMError(f"Gemini API error: {exc}") from exc

    text: str = getattr(resp, "text", "") or ""
    usage = getattr(resp, "usage_metadata", None)
    return LLMResponse(
        provider=provider,
        model=model,
        text=text,
        input_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
    )
