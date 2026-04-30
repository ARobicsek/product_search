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


def _pick_json_text(content: str, reasoning: str) -> str | None:
    """Return whichever of (content, reasoning) parses as JSON, else None.

    Tries the OpenAI-canonical `content` field first, then falls back to
    `reasoning_content` (the Z.AI / GLM-5.1 quirk). Strips ```json fences
    before attempting to parse.
    """
    import json as _json

    def _try(text: str) -> bool:
        if not text:
            return False
        s = text.strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[-1]
            if s.endswith("```"):
                s = s[:-3].strip()
        s = s.removeprefix("json").strip()
        try:
            _json.loads(s)
            return True
        except _json.JSONDecodeError:
            return False

    if _try(content):
        return content
    if _try(reasoning):
        return reasoning
    return None


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
    content = choice.message.content or ""
    reasoning = (
        getattr(choice.message, "reasoning_content", None)
        or getattr(choice.message, "reasoning", None)
        or ""
    )

    # Some OpenAI-compatible providers (notably Z.AI / GLM) route the assistant
    # text into `reasoning_content` instead of `content`. Reasoning-style models
    # (e.g. GLM-5.1) may even dump chain-of-thought prose into `content` while
    # placing the structured answer in `reasoning_content`. Pick the field whose
    # contents look right for the requested format.
    if response_format == "json":
        text = _pick_json_text(content, reasoning) or content or reasoning
    else:
        text = content or reasoning

    if not text:
        import sys
        finish_reason = getattr(choice, "finish_reason", "?")
        try:
            raw = choice.message.model_dump()
        except Exception:
            raw = {"_repr": repr(choice.message)[:500]}
        print(
            f"[llm:{provider}/{model}] empty response  "
            f"finish_reason={finish_reason}  raw_message_keys={sorted(raw.keys())}",
            file=sys.stderr,
        )

    usage = resp.usage
    return LLMResponse(
        provider=provider,
        model=model,
        text=text,
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
    )
