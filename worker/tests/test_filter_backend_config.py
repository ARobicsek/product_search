"""Tests for the ai_filter backend config resolution (Phase 42 / ADR-147)."""

from __future__ import annotations

import pytest

from product_search.config import filter_backend_config

_ENV_KEYS = (
    "AI_FILTER_BACKEND",
    "LOCAL_LLM_BASE",
    "LOCAL_LLM_MODEL",
    "LOCAL_LLM_KEY",
    "LOCAL_LLM_IDLE_WAIT_SECS",
    "LOCAL_LLM_MAX_WAIT_SECS",
    "LOCAL_LLM_POLL_SECS",
)


@pytest.fixture(autouse=True)
def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_default_is_anthropic_haiku() -> None:
    cfg = filter_backend_config()
    assert cfg.backend == "anthropic"
    assert cfg.is_local is False
    # Sensible local defaults are still populated (used only when is_local).
    assert cfg.local_model == "qwen-coder"
    assert cfg.local_base.endswith("/v1")
    assert cfg.idle_wait_secs == 300.0
    assert cfg.max_wait_secs == 600.0


def test_local_backend_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    cfg = filter_backend_config()
    assert cfg.is_local is True


def test_backend_value_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "LOCAL")
    assert filter_backend_config().is_local is True


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setenv("LOCAL_LLM_BASE", "http://10.0.0.5:9000/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3.6-27b-mtp")
    monkeypatch.setenv("LOCAL_LLM_KEY", "xyz")
    monkeypatch.setenv("LOCAL_LLM_IDLE_WAIT_SECS", "120")
    monkeypatch.setenv("LOCAL_LLM_MAX_WAIT_SECS", "240")
    monkeypatch.setenv("LOCAL_LLM_POLL_SECS", "5")
    cfg = filter_backend_config()
    assert cfg.local_base == "http://10.0.0.5:9000/v1"
    assert cfg.local_model == "qwen3.6-27b-mtp"
    assert cfg.local_key == "xyz"
    assert cfg.idle_wait_secs == 120.0
    assert cfg.max_wait_secs == 240.0
    assert cfg.poll_secs == 5.0


def test_bad_float_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_IDLE_WAIT_SECS", "not-a-number")
    assert filter_backend_config().idle_wait_secs == 300.0
