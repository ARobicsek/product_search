"""ai_filter backend selection + local fallback chain (Phase 42 / ADR-147)."""

from __future__ import annotations

import pytest

from product_search.llm import local_box
from product_search.validators.ai_filter import _resolve_filter_chain


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "AI_FILTER_BACKEND",
        "LOCAL_LLM_MODEL",
        "LOCAL_LLM_FALLBACK_MODEL",
        "LOCAL_LLM_ALLOW_HAIKU_FALLBACK",
    ):
        monkeypatch.delenv(k, raising=False)


def test_default_backend_is_haiku_only() -> None:
    assert _resolve_filter_chain() == [("anthropic", "claude-haiku-4-5")]


def test_local_chain_is_primary_then_secondary_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)
    # primary qwen-coder, then the secondary local model — NOT Haiku.
    assert _resolve_filter_chain() == [
        ("local", "qwen-coder"),
        ("local", "qwen3.6-27b-mtp"),
    ]


def test_box_unavailable_falls_back_to_haiku_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: False)
    # DEV default: box unreachable/busy → reachability fallback is Haiku.
    assert _resolve_filter_chain() == [("anthropic", "claude-haiku-4-5")]


def test_prod_no_haiku_stays_local_even_when_box_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner prod rule (ADR-147): cost ~0, NO Haiku of any kind. With the flag
    off, even a coordination failure stays local-only (proceed on the box)."""
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setenv("LOCAL_LLM_ALLOW_HAIKU_FALLBACK", "0")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: False)
    chain = _resolve_filter_chain()
    assert chain == [("local", "qwen-coder"), ("local", "qwen3.6-27b-mtp")]
    assert all(p == "local" for p, _ in chain)  # never Haiku


def test_local_models_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.6-27b-mtp")
    monkeypatch.setenv("LOCAL_LLM_FALLBACK_MODEL", "qwen3.5-122b")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)
    assert _resolve_filter_chain() == [
        ("local", "qwen3.6-27b-mtp"),
        ("local", "qwen3.5-122b"),
    ]


def test_no_duplicate_when_fallback_equals_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setenv("LOCAL_LLM_FALLBACK_MODEL", "qwen-coder")  # same as primary
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)
    assert _resolve_filter_chain() == [("local", "qwen-coder")]
