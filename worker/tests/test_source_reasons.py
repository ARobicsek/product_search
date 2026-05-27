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


def test_thin_body_zero_candidates_is_transient() -> None:
    """ADR-098 fix #3: a tiny body (<5 KB) with 0 candidates is TRANSIENT
    ('check the URL'), NOT EMPTY_PAGE ('genuinely has nothing')."""
    out = classify_source_outcome(
        fetched=0, passed=0, diagnostics={"body_len": 1_200},
    )
    assert out.category is OutcomeCategory.TRANSIENT
    assert "small body" in out.message.lower() or "stub" in out.message.lower()


def test_medium_body_zero_candidates_is_empty_page() -> None:
    """A body between THIN_BODY_CEILING and SUBSTANTIVE_BODY_FLOOR (e.g. 20 KB)
    with 0 candidates is still EMPTY_PAGE — only tiny bodies get reclassified."""
    out = classify_source_outcome(
        fetched=0, passed=0, diagnostics={"body_len": 20_000},
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


# --- ADR-098 fix #4: relevance-dominated NO_MATCH ---


def test_no_match_relevance_dominated_warns_mis_scoped() -> None:
    """When fetched>0/passed=0 and dominant_rejection='relevance_check',
    the message says 'mis-scoped', NOT 'loosen your filter'."""
    out = classify_source_outcome(
        fetched=36, passed=0, dominant_rejection="relevance_check",
    )
    assert out.category is OutcomeCategory.NO_MATCH
    assert "mis-scoped" in out.message.lower()
    assert "loosen" not in out.message.lower()


def test_no_match_without_dominant_rejection_uses_default_message() -> None:
    """When dominant_rejection is absent, the default 'loosen your filter'
    guidance still applies."""
    out = classify_source_outcome(fetched=4, passed=0)
    assert out.category is OutcomeCategory.NO_MATCH
    assert "filter" in out.message.lower()


# --- ADR-099: carry-gate WATCHED status ---


def test_watch_gate_skip_reason_classifies_watched() -> None:
    from product_search.source_reasons import WATCH_GATE_REASON_PREFIX

    out = classify_source_outcome(
        fetched=0,
        passed=0,
        skip_reason=f"{WATCH_GATE_REASON_PREFIX} product identifier 'h14ssl' not present on page",
    )
    assert out.category is OutcomeCategory.WATCHED
    assert out.label == "watched"
    # The message must SAY the vendor isn't stocking it (the user's requirement).
    assert "isn't listed" in out.message.lower()
    assert "$0" in out.message


def test_non_watch_skip_reason_still_transient() -> None:
    # A circuit-breaker / budget skip is NOT a carry-gate skip — stays transient.
    out = classify_source_outcome(
        fetched=0,
        passed=0,
        skip_reason="skipped: AlterLab circuit open after 3 consecutive failures",
    )
    assert out.category is OutcomeCategory.TRANSIENT


def test_watch_gate_does_not_override_real_no_match() -> None:
    # If we actually fetched listings (e.g. JSON-LD), a 0-passed outcome is a
    # real NO_MATCH, not WATCHED, even if a stale skip_reason is present.
    from product_search.source_reasons import WATCH_GATE_REASON_PREFIX

    out = classify_source_outcome(
        fetched=3,
        passed=0,
        skip_reason=f"{WATCH_GATE_REASON_PREFIX} 'h14ssl' not present",
    )
    assert out.category is OutcomeCategory.NO_MATCH
