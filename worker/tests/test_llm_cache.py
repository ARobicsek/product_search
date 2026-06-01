"""Prompt-cache plumbing through the LLM seam (ADR-142 / Phase 39).

The ai_filter re-sends a ~16K-token rules system prompt on every 50-listing
batch. ``cache_system=True`` marks that block ``cache_control: ephemeral`` so
batches 2..N read it at the cache rate instead of paying full input price.
These tests pin (1) the SDK-level system-block construction in
``_anthropic.call``, (2) that the real cache token counts surface on
``LLMResponse``, and (3) that ``call_llm`` only forwards the flag to Anthropic.
"""

from __future__ import annotations

from typing import Any

import pytest

from product_search.llm import LLMResponse, Message, call_llm


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeUsage:
    def __init__(self, **kw: int) -> None:
        self.__dict__.update(kw)


class _FakeResp:
    def __init__(self, text: str, usage: _FakeUsage) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = usage


def _patch_sdk(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Replace ``anthropic.Anthropic`` with a fake recording ``create`` kwargs."""
    anthropic = pytest.importorskip("anthropic")

    class _FakeMessages:
        def create(self, **kwargs: Any) -> _FakeResp:
            captured.update(kwargs)
            return _FakeResp(
                "{}",
                _FakeUsage(
                    input_tokens=5,
                    output_tokens=7,
                    cache_read_input_tokens=100,
                    cache_creation_input_tokens=200,
                ),
            )

    class _FakeClient:
        def __init__(self) -> None:
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda: _FakeClient())


def test_cache_system_builds_cache_control_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.llm import _anthropic

    captured: dict[str, Any] = {}
    _patch_sdk(monkeypatch, captured)

    _anthropic.call(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="RULES",
        messages=[Message(role="user", content="hi")],
        cache_system=True,
    )

    system_arg = captured["system"]
    assert isinstance(system_arg, list)
    assert system_arg[0]["type"] == "text"
    assert system_arg[0]["text"] == "RULES"
    assert system_arg[0]["cache_control"] == {"type": "ephemeral"}


def test_no_cache_system_sends_bare_string(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.llm import _anthropic

    captured: dict[str, Any] = {}
    _patch_sdk(monkeypatch, captured)

    _anthropic.call(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="RULES",
        messages=[Message(role="user", content="hi")],
        cache_system=False,
    )

    # Unchanged behaviour for every non-caching caller: a bare string.
    assert captured["system"] == "RULES"


def test_response_carries_real_cache_token_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.llm import _anthropic

    captured: dict[str, Any] = {}
    _patch_sdk(monkeypatch, captured)

    resp = _anthropic.call(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="RULES",
        messages=[Message(role="user", content="hi")],
        cache_system=True,
    )
    assert resp.cache_read_input_tokens == 100
    assert resp.cache_creation_input_tokens == 200
    assert resp.input_tokens == 5
    assert resp.output_tokens == 7


def test_call_llm_forwards_cache_system_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_call(**kwargs: object) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5", text="{}",
            input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr("product_search.llm._anthropic.call", fake_call)
    call_llm(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="s",
        messages=[Message(role="user", content="hi")],
        cache_system=True,
    )
    assert captured["cache_system"] is True


def test_call_llm_does_not_pass_cache_system_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """cache_system is Anthropic-only; other providers must not see the kwarg
    (their ``call`` signatures don't accept it)."""
    captured: dict[str, object] = {}

    def fake_call(**kwargs: object) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(
            provider="openai", model="gpt-4o-mini", text="{}",
            input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr("product_search.llm._openai.call", fake_call)
    call_llm(
        provider="openai",
        model="gpt-4o-mini",
        system="s",
        messages=[Message(role="user", content="hi")],
        cache_system=True,
    )
    assert "cache_system" not in captured


def test_response_cache_fields_default_none() -> None:
    """Constructing an LLMResponse without cache fields leaves them None
    (every existing call site omits them)."""
    resp = LLMResponse(
        provider="glm", model="glm-4.5-flash", text="{}",
        input_tokens=1, output_tokens=1,
    )
    assert resp.cache_read_input_tokens is None
    assert resp.cache_creation_input_tokens is None
