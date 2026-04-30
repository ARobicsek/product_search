"""Runtime configuration read from environment variables.

Centralises ``LLM_SYNTH_PROVIDER`` / ``LLM_SYNTH_MODEL`` (and, later,
``LLM_ONBOARD_*``) so callers don't sprinkle ``os.environ.get`` throughout
the codebase. Defaults reflect the Phase 5 benchmark winner.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYNTH_PROVIDER = "anthropic"
DEFAULT_SYNTH_MODEL = "claude-haiku-4-5"


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
