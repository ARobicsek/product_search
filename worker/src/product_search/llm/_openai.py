"""OpenAI provider (and GLM via base-URL override) for the LLM abstraction.

GLM (Z.AI / Zhipu) exposes an OpenAI-compatible endpoint:
    base_url = "https://open.bigmodel.cn/api/paas/v4/"
    api_key  = GLM_API_KEY env var

ADR-008 notes this as the intended implementation.

Requires: ``openai>=1.30`` (not yet in pyproject.toml — added in Phase 2).
"""

from __future__ import annotations

import os
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
        import openai
    except ImportError as exc:
        raise ImportError(
            "openai SDK not installed. Run: pip install 'openai>=1.30'"
        ) from exc

    # GLM uses a different base URL and API key env var.
    if provider == "glm":
        api_key = os.environ.get("GLM_API_KEY")
        if not api_key:
            raise LLMError("GLM_API_KEY environment variable not set.")
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://open.bigmodel.cn/api/paas/v4/",
        )
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMError("OPENAI_API_KEY environment variable not set.")
        client = openai.OpenAI(api_key=api_key)

    sdk_messages: list[openai.types.chat.ChatCompletionMessageParam] = [
        openai.types.chat.ChatCompletionSystemMessageParam(role="system", content=system),
    ]
    for m in messages:
        if m.role == "user":
            sdk_messages.append(
                openai.types.chat.ChatCompletionUserMessageParam(role="user", content=m.content)
            )
        else:
            sdk_messages.append(
                openai.types.chat.ChatCompletionAssistantMessageParam(
                    role="assistant", content=m.content
                )
            )

    kwargs: dict[str, object] = {"max_tokens": max_tokens}
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=sdk_messages,
            **kwargs,  # type: ignore[call-overload]
        )
    except openai.APIStatusError as exc:
        raise LLMError(
            f"{provider.upper()} API error ({exc.status_code}): {exc.message}"
        ) from exc
    except openai.APIConnectionError as exc:
        raise LLMError(f"{provider.upper()} connection error: {exc}") from exc

    choice = resp.choices[0]
    text = choice.message.content or ""
    usage = resp.usage
    return LLMResponse(
        provider=provider,
        model=model,
        text=text,
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
    )
