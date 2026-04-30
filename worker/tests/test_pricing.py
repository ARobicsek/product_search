"""Tests for the LLM pricing helpers and cli.py's Run cost panel."""

from __future__ import annotations

import pytest

from product_search.cli import _build_run_cost_md
from product_search.llm.pricing import (
    PRICING,
    estimate_cost_usd,
    format_cost_usd,
)


def test_estimate_cost_for_known_anthropic_model() -> None:
    # claude-haiku-4-5 is $1/M input, $5/M output.
    cost = estimate_cost_usd("anthropic", "claude-haiku-4-5", 10_000, 2_000)
    assert cost == pytest.approx(10_000 * 1.0 / 1_000_000 + 2_000 * 5.0 / 1_000_000)


def test_estimate_cost_for_unknown_model_returns_none() -> None:
    assert estimate_cost_usd("anthropic", "claude-some-future-model", 100, 100) is None
    assert estimate_cost_usd("openai", "gpt-9000", 100, 100) is None


def test_estimate_cost_treats_none_tokens_as_zero() -> None:
    cost = estimate_cost_usd("anthropic", "claude-haiku-4-5", None, None)
    assert cost == 0.0


def test_format_cost_usd_handles_unpriced() -> None:
    assert format_cost_usd(None) == "(unpriced)"


def test_format_cost_usd_renders_subcent_marker_below_threshold() -> None:
    assert format_cost_usd(0.00005) == "<$0.0001"


def test_format_cost_usd_four_decimal_default() -> None:
    assert format_cost_usd(0.1234) == "$0.1234"


def test_pricing_table_includes_active_call_sites() -> None:
    """The actively wired call sites must be priced or the Run cost panel is misleading."""
    # ai_filter (ADR-023) and synth (ADR-024)
    assert ("anthropic", "claude-haiku-4-5") in PRICING
    assert ("glm", "glm-4.5-flash") in PRICING
    # Onboarding (ADR-015)
    assert ("anthropic", "claude-sonnet-4-6") in PRICING


# ---------------------------------------------------------------------------
# _build_run_cost_md
# ---------------------------------------------------------------------------


def test_run_cost_panel_renders_each_call_and_total() -> None:
    md = _build_run_cost_md([
        {
            "step": "ai_filter",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "input_tokens": 10_000,
            "output_tokens": 2_000,
        },
        {
            "step": "synth",
            "provider": "glm",
            "model": "glm-4.5-flash",
            "input_tokens": 4_000,
            "output_tokens": 300,
        },
    ])
    assert "**Run cost.**" in md
    assert "ai_filter" in md
    assert "synth" in md
    # Token counts render with thousands separators for readability.
    assert "10,000" in md
    assert "**Total**" in md


def test_run_cost_panel_marks_unpriced_calls() -> None:
    md = _build_run_cost_md([
        {
            "step": "synth",
            "provider": "glm",
            "model": "glm-future-model-9000",
            "input_tokens": 1_000,
            "output_tokens": 1_000,
        },
    ])
    assert "(unpriced)" in md
    # Total annotation surfaces the unpriced gap so the operator notices.
    assert "unpriced" in md.lower()


def test_run_cost_panel_handles_empty_calls() -> None:
    md = _build_run_cost_md([])
    assert "no llm calls" in md.lower()
