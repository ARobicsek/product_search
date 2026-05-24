"""Tests for the ADR-084 source-outcome reason classifier."""

from product_search.source_reasons import (
    OutcomeCategory,
    classify_source_outcome,
)


def test_passed_listings_is_ok() -> None:
    out = classify_source_outcome(fetched=5, passed=3)
    assert out.category is OutcomeCategory.OK
    assert out.is_clean


def test_fetched_but_none_passed_is_no_match() -> None:
    out = classify_source_outcome(fetched=4, passed=0)
    assert out.category is OutcomeCategory.NO_MATCH
    assert "4 listings" in out.message


def test_fetched_one_uses_singular() -> None:
    out = classify_source_outcome(fetched=1, passed=0)
    assert "1 listing " in out.message


def test_known_failure_is_permanent() -> None:
    out = classify_source_outcome(
        fetched=0,
        passed=0,
        known_failure={"severity": "blocker", "summary": "Cloudflare challenge."},
    )
    assert out.category is OutcomeCategory.PERMANENT
    assert "Cloudflare challenge." in out.message


def test_quota_error_is_permanent() -> None:
    out = classify_source_outcome(
        fetched=0,
        passed=0,
        error="AlterLab API issue: HTTP 429 quota or auth error",
    )
    assert out.category is OutcomeCategory.PERMANENT
    assert "quota" in out.message.lower()


def test_skip_reason_is_transient() -> None:
    out = classify_source_outcome(
        fetched=0,
        passed=0,
        skip_reason="skipped: AlterLab circuit open after 3 consecutive failures",
    )
    assert out.category is OutcomeCategory.TRANSIENT


def test_pool_exhausted_is_transient_and_named() -> None:
    out = classify_source_outcome(
        fetched=0,
        passed=0,
        diagnostics={"body_len": 0, "alterlab_pool_exhausted": True},
    )
    assert out.category is OutcomeCategory.TRANSIENT
    assert "pool" in out.message.lower()


def test_alterlab_degraded_is_transient() -> None:
    out = classify_source_outcome(
        fetched=0,
        passed=0,
        diagnostics={"body_len": 30_000, "alterlab_degraded": True},
    )
    assert out.category is OutcomeCategory.TRANSIENT


def test_generic_fetch_error_is_transient() -> None:
    out = classify_source_outcome(
        fetched=0, passed=0, error="ReadTimeout: timed out"
    )
    assert out.category is OutcomeCategory.TRANSIENT
    assert "ReadTimeout" in out.message


def test_substantive_body_zero_candidates_is_parser_gap() -> None:
    out = classify_source_outcome(
        fetched=0, passed=0, diagnostics={"body_len": 80_000},
    )
    assert out.category is OutcomeCategory.PARSER_GAP
    assert "80,000" in out.message


def test_small_body_zero_candidates_is_empty_page() -> None:
    out = classify_source_outcome(
        fetched=0, passed=0, diagnostics={"body_len": 1_200},
    )
    assert out.category is OutcomeCategory.EMPTY_PAGE


def test_no_diagnostics_zero_candidates_is_empty_page() -> None:
    # A skipped-before-fetch source with no skip_reason recorded falls here.
    out = classify_source_outcome(fetched=0, passed=0)
    assert out.category is OutcomeCategory.EMPTY_PAGE


def test_fetched_listings_beats_degraded_signal() -> None:
    # If we actually got listings, the fetch worked — it's NO_MATCH, not a
    # transient failure, even if a later escalation rung flagged degraded.
    out = classify_source_outcome(
        fetched=2,
        passed=0,
        diagnostics={"body_len": 50_000, "alterlab_degraded": True},
    )
    assert out.category is OutcomeCategory.NO_MATCH
