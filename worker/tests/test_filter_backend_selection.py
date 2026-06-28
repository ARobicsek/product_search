"""ai_filter backend selection + Haiku fallback (Phase 42 / ADR-147)."""

from __future__ import annotations

import pytest

from product_search.llm import local_box
from product_search.validators.ai_filter import _resolve_filter_backend


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_FILTER_BACKEND", raising=False)


def test_default_backend_is_haiku() -> None:
    assert _resolve_filter_backend() == ("anthropic", "claude-haiku-4-5")


def test_local_backend_when_coordination_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)
    assert _resolve_filter_backend() == ("local", "qwen-coder")


def test_falls_back_to_haiku_when_coordination_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: False)
    assert _resolve_filter_backend() == ("anthropic", "claude-haiku-4-5")


def test_local_model_override_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.6-27b-mtp")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)
    assert _resolve_filter_backend() == ("local", "qwen3.6-27b-mtp")
