"""Runtime configuration read from environment variables.

Centralises ``LLM_SYNTH_PROVIDER`` / ``LLM_SYNTH_MODEL`` (and, later,
``LLM_ONBOARD_*``) so callers don't sprinkle ``os.environ.get`` throughout
the codebase. Defaults reflect the Phase 5 benchmark winner.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYNTH_PROVIDER = "glm"
DEFAULT_SYNTH_MODEL = "glm-4.5-flash"


@dataclass(frozen=True)
class SynthConfig:
    provider: str
    model: str


def synth_config() -> SynthConfig:
    """Resolve the synthesizer's (provider, model) from environment.

    Falls back to the Phase 5 benchmark default if env vars are unset.
    """
    return SynthConfig(
        provider=os.environ.get("LLM_SYNTH_PROVIDER", DEFAULT_SYNTH_PROVIDER),
        model=os.environ.get("LLM_SYNTH_MODEL", DEFAULT_SYNTH_MODEL),
    )


# --- ai_filter backend (Phase 42 / ADR-147) ---------------------------------
#
# The relevance filter (validators/ai_filter.py) is the ONLY LLM call a v2 run
# makes (ADR-145). By default it runs on Anthropic Haiku 4.5. Setting
# ``AI_FILTER_BACKEND=local`` routes it to the owner's home GPU box
# (llama-swap, OpenAI-compatible) at ~zero cost, behind the polite
# shared-server coordination layer in ``llm/local_box.py``. Haiku stays the
# default AND the fallback (coordination timeout / box unreachable → Haiku),
# so a scheduled/on-demand run never hangs or fails because the box is busy.
#
# This is a LOCAL/dev opt-in: GitHub-Actions→home-box reachability is a
# separate, still-deferred decision (ADR-145/146), so prod leaves
# ``AI_FILTER_BACKEND`` unset and stays on Haiku.
DEFAULT_AI_FILTER_BACKEND = "anthropic"
DEFAULT_LOCAL_LLM_BASE = "http://100.68.68.101:8080/v1"
DEFAULT_LOCAL_LLM_MODEL = "qwen-coder"  # ADR-146 recommended remainder judge
DEFAULT_LOCAL_LLM_KEY = "dummy"  # llama.cpp ignores the key; any non-empty string

# Polite-coordination defaults (seconds). The owner's rule (ADR-147): join an
# already-loaded qwen-coder (or an idle box) immediately; if a DIFFERENT model
# is loaded, wait until it has had no active inference for ``IDLE_WAIT`` before
# swapping; bound the total wait by ``MAX_WAIT`` then fall back to Haiku.
DEFAULT_LOCAL_LLM_IDLE_WAIT_SECS = 300.0
DEFAULT_LOCAL_LLM_MAX_WAIT_SECS = 600.0
DEFAULT_LOCAL_LLM_POLL_SECS = 15.0


@dataclass(frozen=True)
class FilterBackendConfig:
    """Resolved ai_filter backend selection + local-box coordination knobs."""

    backend: str  # "anthropic" (default) | "local"
    local_base: str
    local_model: str
    local_key: str
    idle_wait_secs: float
    max_wait_secs: float
    poll_secs: float

    @property
    def is_local(self) -> bool:
        return self.backend.strip().lower() == "local"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def filter_backend_config() -> FilterBackendConfig:
    """Resolve the ai_filter backend (and local-box coordination knobs) from env.

    Unset env → the Anthropic-Haiku default (prod behaviour is unchanged).
    """
    return FilterBackendConfig(
        backend=os.environ.get("AI_FILTER_BACKEND", DEFAULT_AI_FILTER_BACKEND),
        local_base=os.environ.get("LOCAL_LLM_BASE", DEFAULT_LOCAL_LLM_BASE),
        local_model=os.environ.get("LOCAL_LLM_MODEL", DEFAULT_LOCAL_LLM_MODEL),
        local_key=os.environ.get("LOCAL_LLM_KEY", DEFAULT_LOCAL_LLM_KEY),
        idle_wait_secs=_env_float("LOCAL_LLM_IDLE_WAIT_SECS", DEFAULT_LOCAL_LLM_IDLE_WAIT_SECS),
        max_wait_secs=_env_float("LOCAL_LLM_MAX_WAIT_SECS", DEFAULT_LOCAL_LLM_MAX_WAIT_SECS),
        poll_secs=_env_float("LOCAL_LLM_POLL_SECS", DEFAULT_LOCAL_LLM_POLL_SECS),
    )
