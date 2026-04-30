"""Tests for the ai_filter response-parser robustness.

GLM-5.1 (and GLM 4.5 Flash before it) frequently emit bare JSON lists even when
the prompt asks for an object. Yesterday's local LLM trace showed GLM returning
``[0]`` for a prompt that asked for ``{"indices": [...]}``. The earlier strict
parser silently dropped every listing on a stylistic difference; this suite
pins the lenient shapes ai_filter must accept.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from product_search.llm import LLMResponse
from product_search.profile import Profile
from product_search.validators import ai_filter as ai_filter_mod
from tests.test_phase2 import _make_listing
from tests.test_profile import VALID_PROFILE


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate(VALID_PROFILE)


@pytest.fixture(autouse=True)
def _disable_fixture_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the LLM code path; other test files set WORKER_USE_FIXTURES=1 at import."""
    monkeypatch.delenv("WORKER_USE_FIXTURES", raising=False)


def _stub_response(text: str) -> Any:
    def _call(**_: object) -> LLMResponse:
        return LLMResponse(
            provider="glm", model="glm-5.1", text=text,
            input_tokens=0, output_tokens=0,
        )
    return _call


def test_accepts_canonical_evaluations_object(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    listings = [_make_listing(), _make_listing(title="other")]
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(
        '{"evaluations": ['
        '{"index": 0, "pass": true, "reason": "matches all rules"},'
        '{"index": 1, "pass": false, "reason": "wrong form factor"}'
        ']}'
    ))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert len(out) == 1
    assert out[0] is listings[0]


def test_accepts_bare_array_of_evaluations(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """GLM often drops the wrapper key and emits a bare array. Must still parse."""
    listings = [_make_listing(), _make_listing(title="other")]
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(
        '['
        '{"index": 0, "pass": false, "reason": "fails rule X"},'
        '{"index": 1, "pass": true, "reason": "ok"}'
        ']'
    ))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert [lst.title for lst in out] == ["other"]


def test_accepts_legacy_indices_object(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    listings = [_make_listing(), _make_listing(title="other")]
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response('{"indices": [1]}'))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert [lst.title for lst in out] == ["other"]


def test_accepts_bare_list_of_integers(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    listings = [_make_listing(), _make_listing(title="other")]
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response("[0]"))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert [lst.title for lst in out] == [listings[0].title]


def test_returns_empty_on_unrecognised_shape(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    listings = [_make_listing()]
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response('{"results": "anything"}'))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert out == []


def test_returns_empty_on_invalid_json(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    listings = [_make_listing()]
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response("not json at all"))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert out == []
