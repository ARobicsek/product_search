"""Tests for the OpenAI wrapper's JSON-field-pick logic.

Reasoning models (e.g. GLM-5.1 via the Z.AI shim) sometimes route the
chain-of-thought prose into `content` while putting the actual JSON answer
into `reasoning_content`. The wrapper must pick whichever field actually
contains parseable JSON.
"""

from __future__ import annotations

from product_search.llm._openai import _pick_json_text


def test_prefers_content_when_content_is_valid_json() -> None:
    assert _pick_json_text('{"ok": true}', "some reasoning prose") == '{"ok": true}'


def test_falls_back_to_reasoning_when_content_is_prose() -> None:
    """The GLM-5.1 reasoning-model failure mode that broke the prod ai_filter run."""
    content = "The user wants to filter products. Target: 8x32GB modules.\nLet me think..."
    reasoning = '{"evaluations": [{"index": 0, "pass": true, "reason": "ok"}]}'
    assert _pick_json_text(content, reasoning) == reasoning


def test_returns_none_when_neither_field_parses() -> None:
    assert _pick_json_text("just prose", "more prose") is None


def test_strips_markdown_code_fences() -> None:
    fenced = '```json\n{"ok": true}\n```'
    assert _pick_json_text(fenced, "") == fenced


def test_handles_empty_content_with_reasoning_json() -> None:
    assert _pick_json_text("", '{"x": 1}') == '{"x": 1}'


def test_handles_empty_reasoning_with_content_json() -> None:
    assert _pick_json_text('{"y": 2}', "") == '{"y": 2}'
