"""Tests for the ai_filter response-parser robustness.

GLM-5.1 (and GLM 4.5 Flash before it) frequently emit bare JSON lists even when
the prompt asks for an object. Yesterday's local LLM trace showed GLM returning
``[0]`` for a prompt that asked for ``{"indices": [...]}``. The earlier strict
parser silently dropped every listing on a stylistic difference; this suite
pins the lenient shapes ai_filter must accept.
"""

from __future__ import annotations

from pathlib import Path
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
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Force the LLM code path AND redirect filter-log writes to a temp dir.

    Other test files set ``WORKER_USE_FIXTURES=1`` at module import, which
    short-circuits ai_filter; we need it off here. We also redirect both the
    daily filter log and the per-product diagnostic log so pytest runs don't
    pollute ``worker/data/filter_logs/`` or ``reports/<slug>/`` on the
    developer's machine (and don't accidentally commit test sentinel rows).
    """
    monkeypatch.delenv("WORKER_USE_FIXTURES", raising=False)
    log_path = tmp_path / "filter_log.jsonl"
    per_product_path = tmp_path / "per_product_filter_log.jsonl"
    monkeypatch.setattr(ai_filter_mod, "_filter_log_path", lambda: log_path)
    monkeypatch.setattr(
        ai_filter_mod, "_per_product_filter_log_path", lambda _slug: per_product_path
    )


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


def test_rejects_truncated_inner_eval_object(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """A truncated outer envelope (the response cut off mid-array) used to
    leak the first complete inner evaluation object, silently dropping every
    listing. The hardened parser rejects ``{"index":..., "pass":...}`` shapes
    when they aren't wrapped by ``evaluations``/``indices``.
    """
    listings = [_make_listing(), _make_listing(title="other")]
    # The response starts with the outer envelope but cuts off before the
    # closing brace — only the first inner eval object is fully present.
    truncated = (
        '{"evaluations": [\n'
        '  {"index": 0, "pass": true, "reason": "exact match"},\n'
        '  {"index": 1, "pass": fa'  # cut off mid-token
    )
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(truncated))
    out = ai_filter_mod.ai_filter(listings, profile)
    # No listings pass — the parse failure short-circuits to empty.
    assert out == []
    # Sentinel diagnostic recorded so the report shows why.
    assert any(e.get("index") == -1 for e in ai_filter_mod.LAST_RUN_LOG)


def test_batches_large_listing_set(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """100 listings get split into multiple LLM calls (batch size 50). Each
    batch's response uses LOCAL indices (0..N-1 for that batch), and the
    public function maps them back to global indices before returning.
    """
    listings = [_make_listing(title=f"listing-{i}") for i in range(100)]

    call_count = {"n": 0}

    def stubbed(**kw: object) -> LLMResponse:
        # Each batch has up to 50 entries; we pass every odd local index.
        user_msg = kw["messages"][0].content  # type: ignore[index, attr-defined]
        import json as _json
        batch = _json.loads(user_msg)
        evals = [
            {"index": item["index"], "pass": item["index"] % 2 == 1, "reason": "ok"}
            for item in batch
        ]
        call_count["n"] += 1
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"evaluations": evals}),
            input_tokens=10, output_tokens=20,
        )

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    out = ai_filter_mod.ai_filter(listings, profile)

    # Two LLM calls (50 + 50).
    assert call_count["n"] == 2
    # Odd global indices passed.
    expected_titles = [f"listing-{i}" for i in range(100) if i % 2 == 1]
    assert [lst.title for lst in out] == expected_titles
    # Usage is summed across batches.
    assert ai_filter_mod.LAST_RUN_USAGE is not None
    assert ai_filter_mod.LAST_RUN_USAGE["input_tokens"] == 20
    assert ai_filter_mod.LAST_RUN_USAGE["output_tokens"] == 40


def test_local_parse_failure_falls_back_to_secondary_local(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A primary-local parse failure must degrade to the SECONDARY LOCAL model.

    Phase 42 / ADR-147: schema-constrained decoding makes a primary failure rare,
    but if it happens the in-run fallback is another local model (qwen3.6-27b-mtp)
    — NOT Haiku (owner: too expensive). The run must still complete, not zero out.
    """
    from product_search.llm import local_box

    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)

    listings = [_make_listing(), _make_listing(title="other")]

    def stubbed(**kw: object) -> LLMResponse:
        model = kw["model"]
        if model == "qwen-coder":  # primary: truncated/garbage JSON
            return LLMResponse(provider="local", model="qwen-coder",
                               text='{"evaluations": [{"index": 0,', input_tokens=5, output_tokens=2)
        # secondary local model produces a valid response
        return LLMResponse(provider="local", model="qwen3.6-27b-mtp",
                          text='{"evaluations": [{"index": 0, "pass": true, "reason": "ok"},'
                               '{"index": 1, "pass": false, "reason": "no"}]}',
                          input_tokens=10, output_tokens=20)

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    out = ai_filter_mod.ai_filter(listings, profile)

    # Secondary local model's verdict is honored (1 survivor); no zeroed run, no Haiku.
    assert len(out) == 1
    assert out[0] is listings[0]
    assert ai_filter_mod.LAST_RUN_USAGE is not None
    assert ai_filter_mod.LAST_RUN_USAGE["provider"] == "local"
    assert ai_filter_mod.LAST_RUN_USAGE["model"] == "qwen3.6-27b-mtp"


def test_local_chain_exhaustion_fires_notification(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every LOCAL model in the chain fails, fire an operational alert so
    the owner can investigate (ADR-147 prod requirement)."""
    from product_search.llm import local_box
    from product_search.validators import ai_filter as af

    monkeypatch.setenv("AI_FILTER_BACKEND", "local")
    monkeypatch.setattr(local_box, "coordinate_local_access", lambda *a, **k: True)

    # Every local model returns unparseable JSON → chain exhausted.
    def always_bad(**kw: object) -> LLMResponse:
        return LLMResponse(provider="local", model=str(kw["model"]),
                           text="not json", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(ai_filter_mod, "call_llm", always_bad)

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(af, "_notify_filter_failure", lambda slug, reason: sent.append((slug, reason)))

    out = ai_filter_mod.ai_filter([_make_listing()], profile)
    assert out == []  # zeroed (no Haiku in prod-style local-only chain)
    assert len(sent) == 1
    assert sent[0][0] == profile.slug


def test_system_prompt_falls_back_to_display_name_when_description_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-074 followup #2: when `description` is omitted (now allowed by the
    schema), the AI filter system prompt must fall back to `display_name` so
    the "Description:" line stays meaningful (never blank).
    """
    import copy

    p_dict = copy.deepcopy(VALID_PROFILE)
    p_dict.pop("description")  # omit entirely — defaults to ""
    profile_no_desc = Profile.model_validate(p_dict)
    assert profile_no_desc.description == ""

    captured: dict[str, str] = {}

    def stubbed(**kw: object) -> LLMResponse:
        captured["system"] = str(kw.get("system", ""))
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text='{"evaluations": [{"index": 0, "pass": true, "reason": "ok"}]}',
            input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    ai_filter_mod.ai_filter([_make_listing()], profile_no_desc)

    # The literal "Description: " line is followed by display_name, not blank.
    assert f"Description: {profile_no_desc.display_name}" in captured["system"]
    # And the line is NOT empty (the regression we are pinning).
    assert "Description: \n" not in captured["system"]


def test_filter_opts_into_prompt_caching(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-142: the filter passes ``cache_system=True`` so the stable rules
    system block is cached across batches."""
    captured: dict[str, object] = {}

    def stubbed(**kw: object) -> LLMResponse:
        captured.update(kw)
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text='{"evaluations": [{"index": 0, "pass": true}]}',
            input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    ai_filter_mod.ai_filter([_make_listing()], profile)
    assert captured["cache_system"] is True


def test_cache_tokens_summed_into_last_run_usage(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-142: real per-batch cache read/write counts are summed into
    LAST_RUN_USAGE so the cost panel can price the split honestly."""
    listings = [_make_listing(title=f"listing-{i}") for i in range(60)]  # 2 batches

    def stubbed(**kw: object) -> LLMResponse:
        import json as _json
        batch = _json.loads(kw["messages"][0].content)  # type: ignore[index, attr-defined]
        evals = [{"index": item["index"], "pass": True} for item in batch]
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"evaluations": evals}),
            input_tokens=10, output_tokens=20,
            cache_read_input_tokens=500, cache_creation_input_tokens=300,
        )

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    ai_filter_mod.ai_filter(listings, profile)

    assert ai_filter_mod.LAST_RUN_USAGE is not None
    # Two batches → cache counts summed.
    assert ai_filter_mod.LAST_RUN_USAGE["cache_read_input_tokens"] == 1000
    assert ai_filter_mod.LAST_RUN_USAGE["cache_creation_input_tokens"] == 600


def test_prompt_no_longer_requests_pass_reasons(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-142 Step 4: pass-reasons are logged but never surfaced, so the
    prompt now asks the model to omit ``reason`` for passing items."""
    captured: dict[str, str] = {}

    def stubbed(**kw: object) -> LLMResponse:
        captured["system"] = str(kw.get("system", ""))
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text='{"evaluations": [{"index": 0, "pass": true}]}',
            input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    ai_filter_mod.ai_filter([_make_listing()], profile)
    sys_prompt = captured["system"]
    assert 'OMIT "reason"' in sys_prompt
    assert "false" in sys_prompt  # "REQUIRED ONLY when \"pass\" is false"


def test_survivor_set_identical_with_or_without_pass_reasons(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-142 determinism guardrail: dropping pass-reasons must not change
    which listings survive. The verdict is decided by the ``pass`` boolean;
    the parser tolerates a missing ``reason`` on passing entries."""
    listings = [_make_listing(title=f"listing-{i}") for i in range(5)]

    # Same pass/fail pattern, two output shapes: with and without pass-reasons.
    with_reasons = (
        '{"evaluations": ['
        '{"index": 0, "pass": true, "reason": "matches base model"},'
        '{"index": 1, "pass": false, "reason": "relevance_check: accessory"},'
        '{"index": 2, "pass": true, "reason": "matches base model"},'
        '{"index": 3, "pass": false, "reason": "title_excludes: refurbished"},'
        '{"index": 4, "pass": true, "reason": "matches base model"}'
        ']}'
    )
    without_reasons = (
        '{"evaluations": ['
        '{"index": 0, "pass": true},'
        '{"index": 1, "pass": false, "reason": "relevance_check: accessory"},'
        '{"index": 2, "pass": true},'
        '{"index": 3, "pass": false, "reason": "title_excludes: refurbished"},'
        '{"index": 4, "pass": true}'
        ']}'
    )

    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(with_reasons))
    before = [lst.title for lst in ai_filter_mod.ai_filter(list(listings), profile)]

    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(without_reasons))
    after = [lst.title for lst in ai_filter_mod.ai_filter(list(listings), profile)]

    assert before == after == ["listing-0", "listing-2", "listing-4"]


def test_extracts_condition_into_attrs_when_unset(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 40: Serper carries no condition (``condition=""``). When the model
    extracts one from the title it lands in ``attrs.condition`` for display."""
    lst = _make_listing(condition="", attrs={}, source="serper_shopping")
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(
        '{"evaluations": [{"index": 0, "pass": true, '
        '"extracted_features": {"condition": "new", "color": "blue"}}]}'
    ))
    out = ai_filter_mod.ai_filter([lst], profile)
    assert len(out) == 1
    assert out[0].attrs["condition"] == "new"
    assert out[0].attrs["color"] == "blue"


def test_structured_condition_not_overridden_by_extracted(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 40: a real structured ``condition`` (e.g. from eBay) wins — a
    title-derived guess never overwrites it (mirrors brand/quantity)."""
    lst = _make_listing(condition="new", attrs={})
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(
        '{"evaluations": [{"index": 0, "pass": true, '
        '"extracted_features": {"condition": "used"}}]}'
    ))
    out = ai_filter_mod.ai_filter([lst], profile)
    assert out[0].condition == "new"
    assert "condition" not in out[0].attrs


def test_prompt_requests_condition_extraction(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 40: the extraction prompt now lists ``condition`` with normalize
    guidance so resale items surface new/used/refurbished."""
    captured: dict[str, str] = {}

    def stubbed(**kw: object) -> LLMResponse:
        captured["system"] = str(kw.get("system", ""))
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text='{"evaluations": [{"index": 0, "pass": true}]}',
            input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr(ai_filter_mod, "call_llm", stubbed)
    ai_filter_mod.ai_filter([_make_listing()], profile)
    sys_prompt = captured["system"]
    assert "condition" in sys_prompt
    assert '"open box"' in sys_prompt  # the normalize vocabulary is present


def test_tolerates_prose_preamble_before_json(profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
    """A prose preamble before the JSON object must not zero out the run.

    GLM-4.5-Flash was observed (2026-04-30 prod run) emitting:
        "Let me analyze the products one by one according to the rules
         provided. First, let's review the rules: ... {"evaluations":[...]}"
    despite response_format=json_object. The parser walks from the first '{'
    and decodes the longest valid JSON at that position.
    """
    listings = [_make_listing(), _make_listing(title="other")]
    preamble = (
        "Let me analyze the products one by one according to the rules.\n"
        "First, let's review what we have:\n\n"
    )
    body = (
        '{"evaluations": ['
        '{"index": 0, "pass": true, "reason": "ok"},'
        '{"index": 1, "pass": false, "reason": "wrong"}'
        ']}'
    )
    monkeypatch.setattr(ai_filter_mod, "call_llm", _stub_response(preamble + body))
    out = ai_filter_mod.ai_filter(listings, profile)
    assert [lst.title for lst in out] == [listings[0].title]


# ---------------------------------------------------------------------------
# Phase 41 / ADR-145: the system prompt must list ONLY rules the profile carries
# so the model stops fabricating condition rejections (e.g. dropping the exact
# RAM part HMCG84AGBRA191N for a "Refurbished" the profile's empty condition_in
# allowed). Tests target the buildable ``_build_system_prompt`` directly.
# ---------------------------------------------------------------------------


def _profile_with_filters(spec_filters: list[dict[str, Any]]) -> Profile:
    import copy

    raw = copy.deepcopy(VALID_PROFILE)
    raw["spec_filters"] = spec_filters
    return Profile.model_validate(raw)


def test_build_prompt_omits_condition_explanation_when_no_rule() -> None:
    # No condition_in rule present → its rule explanation must be absent, so the
    # model is never primed to reject on condition. Extraction guidance stays.
    prof = _profile_with_filters([{"rule": "in_stock"}])
    prompt = ai_filter_mod._build_system_prompt(prof, ["price", "condition"])
    assert "condition_in {values" not in prompt
    # Extraction is independent of the rule and must remain available for display.
    assert '"open box"' in prompt
    assert "extracted_features" in prompt


def test_build_prompt_includes_condition_explanation_when_rule_present() -> None:
    prof = _profile_with_filters(
        [{"rule": "condition_in", "values": ["new"]}, {"rule": "in_stock"}]
    )
    prompt = ai_filter_mod._build_system_prompt(prof, ["price", "condition"])
    assert "condition_in {values" in prompt
    # Anti-fabrication wording: explicit-cue-only, never inferred.
    assert "EXPLICITLY" in prompt
    assert "never\n  infer a condition from the absence" in prompt


def test_build_prompt_omits_absent_rule_explanations() -> None:
    # A minimal profile carries only in_stock; the form_factor/speed/title rules
    # must not appear (they primed the model on constraints that don't exist).
    prof = _profile_with_filters([{"rule": "in_stock"}])
    prompt = ai_filter_mod._build_system_prompt(prof, [])
    for absent in ("form_factor_in {values", "speed_mts_min {value", "title_excludes {values"):
        assert absent not in prompt
    # relevance_check is always on, and in_stock is present here.
    assert "relevance_check" in prompt
    assert "- in_stock:" in prompt


def test_build_prompt_extraction_is_display_only() -> None:
    prof = _profile_with_filters([{"rule": "in_stock"}])
    prompt = ai_filter_mod._build_system_prompt(prof, ["price", "condition"])
    assert "DISPLAY ONLY" in prompt
    assert 'NEVER by itself cause "pass": false' in prompt


def test_build_prompt_present_ram_rules_preserved() -> None:
    # The default fixture's RAM rules must still be fully explained (no regression
    # for present rules — only ABSENT rules are dropped).
    prof = Profile.model_validate(VALID_PROFILE)  # form_factor_in, ecc_required, in_stock
    prompt = ai_filter_mod._build_system_prompt(prof, [])
    assert "form_factor_in {values" in prompt
    assert "- ecc_required:" in prompt
    assert "- in_stock:" in prompt
