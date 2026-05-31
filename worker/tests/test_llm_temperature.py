"""``call_llm`` must forward ``temperature`` to the provider (ADR-132).

At provider-default sampling (~1.0) the ai_filter was a run-to-run lottery
(Haiku DDR5 pass-count swung 35/28/19); the filter now passes ``temperature=0``.
This pins the plumbing: the kwarg reaches the provider ``call`` unchanged, and
``None`` is forwarded as ``None`` (leave at provider default).
"""

from __future__ import annotations

import pytest

from product_search.llm import LLMResponse, Message, call_llm


@pytest.fixture
def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_call(**kwargs: object) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5", text="{}",
            input_tokens=1, output_tokens=1,
        )

    # call_llm imports the provider's ``call`` lazily by module attribute, so
    # patching the module attribute is enough to intercept it.
    monkeypatch.setattr("product_search.llm._anthropic.call", fake_call)
    return captured


def test_temperature_forwarded_to_provider(_capture: dict[str, object]) -> None:
    call_llm(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="s",
        messages=[Message(role="user", content="hi")],
        temperature=0,
    )
    assert _capture["temperature"] == 0


def test_temperature_defaults_to_none(_capture: dict[str, object]) -> None:
    call_llm(
        provider="anthropic",
        model="claude-haiku-4-5",
        system="s",
        messages=[Message(role="user", content="hi")],
    )
    assert _capture["temperature"] is None
