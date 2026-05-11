"""Phase 17 Part C — alerts evaluator tests.

Pin the transition semantics from PHASES.md §"Phase 17 — Schedule editor +
alerts UI", task 6: each rule fires only when the condition flips
``false → true`` between runs (or on the first observation when there is no
previous run). The alternative ("fire on every matching run") would spam the
user every day until the condition cleared — these tests pin the no-spam
behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from product_search.alerts import (
    FiredAlert,
    evaluate_alerts,
    listing_host,
    load_previous_run,
    previous_run_csv,
    render_audit_panel,
)
from product_search.models import Listing
from product_search.profile import PriceBelowAlert, VendorSeenAlert
from product_search.storage.csv_dump import write_snapshot_csv


# ---------------------------------------------------------------------------
# Listing factory — minimal valid Listing for evaluator tests
# ---------------------------------------------------------------------------


def _mk_listing(
    *,
    url: str = "https://example.com/p/123",
    unit_price_usd: float = 100.0,
    condition: str = "new",
    source: str = "ebay_search",
    attrs: dict | None = None,
) -> Listing:
    return Listing(
        source=source,
        url=url,
        title="Test listing",
        fetched_at=datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC),
        brand="Acme",
        mpn="ACME-1",
        attrs=attrs or {},
        condition=condition,
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=unit_price_usd,
        kit_price_usd=None,
        quantity_available=1,
        seller_name="someone",
        seller_rating_pct=99.0,
        seller_feedback_count=100,
        ship_from_country="US",
    )


# ---------------------------------------------------------------------------
# evaluate_alerts — happy paths and no-ops
# ---------------------------------------------------------------------------


def test_empty_rules_returns_empty() -> None:
    assert evaluate_alerts([], [_mk_listing()], None) == []


def test_price_below_fires_on_first_run_when_below() -> None:
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    fired = evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None)
    assert len(fired) == 1
    assert "150.00" in fired[0].headline
    assert "200.00" in fired[0].headline


def test_price_below_does_not_fire_when_at_or_above_threshold() -> None:
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    # Exactly at threshold -> not below
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=200.0)], None) == []
    # Above threshold
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=250.0)], None) == []


def test_price_below_does_not_fire_when_previous_was_already_below() -> None:
    """The transition rule: if previous run's cheapest was already below the
    threshold, the user has already been notified — don't re-fire."""
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    current = [_mk_listing(unit_price_usd=150.0)]
    previous = [_mk_listing(unit_price_usd=160.0)]
    assert evaluate_alerts([rule], current, previous) == []


def test_price_below_fires_when_previous_was_at_or_above_threshold() -> None:
    """At-or-above on the previous run is the canonical pre-condition for a
    transition fire (per PHASES.md task 6)."""
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    # Previous exactly at threshold (not strictly below) -> still fires
    current = [_mk_listing(unit_price_usd=150.0)]
    previous_at = [_mk_listing(unit_price_usd=200.0)]
    assert len(evaluate_alerts([rule], current, previous_at)) == 1
    # Previous above threshold -> fires
    previous_above = [_mk_listing(unit_price_usd=250.0)]
    assert len(evaluate_alerts([rule], current, previous_above)) == 1


def test_price_below_does_not_fire_when_no_eligible_listings() -> None:
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    assert evaluate_alerts([rule], [], None) == []


def test_price_below_with_condition_filter_only_counts_matching_condition() -> None:
    """A 'new'-conditioned rule must ignore used/refurbished cheaper listings."""
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, condition="new"
    )
    # Used listing is cheap but the rule is filtered to 'new' — the only new
    # listing is above threshold, so no fire.
    current = [
        _mk_listing(unit_price_usd=50.0, condition="used"),
        _mk_listing(unit_price_usd=250.0, condition="new"),
    ]
    assert evaluate_alerts([rule], current, None) == []
    # Now make the new listing cheap — it should fire.
    current = [
        _mk_listing(unit_price_usd=50.0, condition="used"),
        _mk_listing(unit_price_usd=150.0, condition="new"),
    ]
    fired = evaluate_alerts([rule], current, None)
    assert len(fired) == 1
    assert "(new)" in fired[0].headline


def test_price_below_transition_is_per_condition() -> None:
    """Previous-run check is also condition-filtered: a previous 'used'
    listing below the new-conditioned threshold doesn't suppress the fire."""
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, condition="new"
    )
    current = [_mk_listing(unit_price_usd=150.0, condition="new")]
    # Previous cheapest was a 'used' below threshold — but not eligible
    # for this rule, so the new-condition transition still fires.
    previous = [_mk_listing(unit_price_usd=100.0, condition="used")]
    assert len(evaluate_alerts([rule], current, previous)) == 1


# ---------------------------------------------------------------------------
# vendor_seen
# ---------------------------------------------------------------------------


def test_vendor_seen_fires_on_first_run_when_present() -> None:
    rule = VendorSeenAlert(kind="vendor_seen", host="amazon.com")
    current = [_mk_listing(url="https://www.amazon.com/dp/X")]
    fired = evaluate_alerts([rule], current, None)
    assert len(fired) == 1
    assert "amazon.com" in fired[0].headline


def test_vendor_seen_does_not_fire_when_host_absent() -> None:
    rule = VendorSeenAlert(kind="vendor_seen", host="amazon.com")
    current = [_mk_listing(url="https://www.ebay.com/itm/X")]
    assert evaluate_alerts([rule], current, None) == []


def test_vendor_seen_does_not_re_fire_when_previously_present() -> None:
    """Transition: amazon.com was already present last run — no fire."""
    rule = VendorSeenAlert(kind="vendor_seen", host="amazon.com")
    current = [_mk_listing(url="https://www.amazon.com/dp/X")]
    previous = [_mk_listing(url="https://amazon.com/dp/Y")]
    assert evaluate_alerts([rule], current, previous) == []


def test_vendor_seen_fires_when_host_returned_after_a_zero_run() -> None:
    rule = VendorSeenAlert(kind="vendor_seen", host="amazon.com")
    current = [_mk_listing(url="https://www.amazon.com/dp/X")]
    previous = [_mk_listing(url="https://www.ebay.com/itm/X")]
    assert len(evaluate_alerts([rule], current, previous)) == 1


def test_vendor_seen_canonical_match_strips_www_and_lowercases() -> None:
    """ADR-020 canonical match: 'AMAZON.COM' / 'www.amazon.com' / 'amazon.com'
    must all collide so a user typing any spelling gets the same behavior."""
    rule = VendorSeenAlert(kind="vendor_seen", host="AMAZON.COM")
    current = [_mk_listing(url="https://www.AMAZON.com/dp/X")]
    assert len(evaluate_alerts([rule], current, None)) == 1


def test_vendor_seen_uses_attrs_vendor_host_for_universal_ai() -> None:
    """universal_ai listings carry vendor_host in attrs — the URL would
    otherwise be the listing's full path which could share a host with
    other listings emitted by the same source. attrs.vendor_host wins."""
    rule = VendorSeenAlert(kind="vendor_seen", host="audio46.com")
    current = [
        _mk_listing(
            source="universal_ai_search",
            url="https://audio46.com/products/some-headphones",
            attrs={"vendor_host": "audio46.com"},
        )
    ]
    assert len(evaluate_alerts([rule], current, None)) == 1


def test_listing_host_returns_canonical_form() -> None:
    assert listing_host(_mk_listing(url="https://www.Amazon.com/dp/X")) == "amazon.com"
    assert listing_host(_mk_listing(url="https://ebay.com/itm/X")) == "ebay.com"
    # attrs.vendor_host overrides url-derived host
    assert (
        listing_host(
            _mk_listing(
                url="https://example.com/p/X", attrs={"vendor_host": "audio46.com"}
            )
        )
        == "audio46.com"
    )


# ---------------------------------------------------------------------------
# Multiple rules in one evaluation
# ---------------------------------------------------------------------------


def test_multiple_rules_each_evaluated_independently() -> None:
    rules = [
        PriceBelowAlert(kind="price_below", threshold_usd=200.0),
        VendorSeenAlert(kind="vendor_seen", host="amazon.com"),
    ]
    current = [
        _mk_listing(url="https://amazon.com/dp/X", unit_price_usd=150.0),
    ]
    fired = evaluate_alerts(rules, current, None)
    assert len(fired) == 2
    kinds = {fa.rule.kind for fa in fired}
    assert kinds == {"price_below", "vendor_seen"}


# ---------------------------------------------------------------------------
# previous_run_csv / load_previous_run — disk I/O
# ---------------------------------------------------------------------------


def test_previous_run_csv_returns_none_when_no_data_dir(tmp_path: Path) -> None:
    assert previous_run_csv("nonexistent-slug", data_dir=tmp_path / "missing") is None


def test_previous_run_csv_returns_none_when_data_dir_empty(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    assert previous_run_csv("any", data_dir=tmp_path / "data") is None


def test_previous_run_csv_picks_most_recent_excluding_current(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Filenames are zero-padded UTC timestamps, so lexical sort = chronological.
    older = data_dir / "2026-05-10T08-00-00Z.csv"
    newer = data_dir / "2026-05-11T08-00-00Z.csv"
    write_snapshot_csv(older, [_mk_listing(unit_price_usd=180.0)])
    write_snapshot_csv(newer, [_mk_listing(unit_price_usd=160.0)])
    # Without exclusion: returns the newest CSV.
    assert previous_run_csv("any", data_dir=data_dir) == newer
    # When the newest is the current run, fall back to the prior one.
    assert previous_run_csv("any", exclude=newer, data_dir=data_dir) == older


def test_load_previous_run_round_trips_listings(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    csv_path = data_dir / "2026-05-10T08-00-00Z.csv"
    write_snapshot_csv(
        csv_path,
        [_mk_listing(unit_price_usd=180.0), _mk_listing(unit_price_usd=200.0)],
    )
    loaded = load_previous_run("any", data_dir=data_dir)
    assert loaded is not None
    assert len(loaded) == 2
    assert {lst.unit_price_usd for lst in loaded} == {180.0, 200.0}


def test_load_previous_run_returns_none_when_only_current_exists(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    only = data_dir / "2026-05-11T08-00-00Z.csv"
    write_snapshot_csv(only, [_mk_listing()])
    assert load_previous_run("any", exclude=only, data_dir=data_dir) is None


# ---------------------------------------------------------------------------
# render_audit_panel
# ---------------------------------------------------------------------------


def test_render_audit_panel_empty_when_nothing_fired() -> None:
    assert render_audit_panel([], []) == ""


def test_render_audit_panel_lists_each_fire_with_status() -> None:
    fired = [
        FiredAlert(
            rule=PriceBelowAlert(kind="price_below", threshold_usd=200.0),
            headline="Cheapest dropped to $150.00 — alert threshold $200.00",
        ),
        FiredAlert(
            rule=VendorSeenAlert(kind="vendor_seen", host="amazon.com"),
            headline="amazon.com now has at least one passing listing",
        ),
    ]
    panel = render_audit_panel(fired, [True, False])
    assert "## Alerts fired" in panel
    assert "price_below" in panel
    assert "vendor_seen" in panel
    assert "notify=ok" in panel
    assert "notify=failed" in panel
