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
    AlertsState,
    FiredAlert,
    evaluate_alerts,
    listing_host,
    load_alerts_state,
    load_previous_run,
    previous_run_csv,
    render_audit_panel,
    rule_fingerprint,
    save_alerts_state,
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
    is_kit: bool = False,
    kit_module_count: int = 1,
    kit_price_usd: float | None = None,
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
        is_kit=is_kit,
        kit_module_count=kit_module_count,
        unit_price_usd=unit_price_usd,
        kit_price_usd=kit_price_usd,
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


# ---------------------------------------------------------------------------
# ADR-056 — price_below mode: is_below (state-based) vs drops_below (default)
# ---------------------------------------------------------------------------


def test_price_below_mode_defaults_to_drops_below() -> None:
    """Back-compat: a rule with no mode is the old transition behavior."""
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    assert rule.mode == "drops_below"


def test_is_below_fires_even_when_previous_already_below() -> None:
    """The whole point of is_below: a rule created while the price is ALREADY
    below fires on its first run (drops_below would stay silent here)."""
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="is_below"
    )
    current = [_mk_listing(unit_price_usd=150.0)]
    previous = [_mk_listing(unit_price_usd=160.0)]  # already below last run
    state = AlertsState()
    fired = evaluate_alerts([rule], current, previous, state)
    assert len(fired) == 1
    assert "150.00" in fired[0].headline
    # drops_below on the same inputs would NOT fire (the contrast).
    drops = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    assert evaluate_alerts([drops], current, previous, AlertsState()) == []


def test_is_below_does_not_refire_while_staying_below() -> None:
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="is_below"
    )
    state = AlertsState()
    assert len(evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None, state)) == 1
    # Still below on the next run — armed flag is now False, so silent.
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=140.0)], None, state) == []
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=199.0)], None, state) == []


def test_is_below_rearms_when_price_returns_above_then_fires_again() -> None:
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="is_below"
    )
    state = AlertsState()
    assert len(evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None, state)) == 1
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None, state) == []
    # Price climbs back to/above threshold -> re-arm (no notification).
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=250.0)], None, state) == []
    # Next dip fires again.
    fired = evaluate_alerts([rule], [_mk_listing(unit_price_usd=180.0)], None, state)
    assert len(fired) == 1


def test_is_below_rearms_when_no_eligible_listings() -> None:
    """No listing for the condition counts as 'not below' -> re-arm."""
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="is_below", condition="new"
    )
    state = AlertsState()
    assert len(evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0, condition="new")], None, state)) == 1
    # Only a used listing now — not eligible -> re-armed.
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=50.0, condition="used")], None, state) == []
    fired = evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0, condition="new")], None, state)
    assert len(fired) == 1


def test_is_below_with_none_state_treats_rule_as_armed_once() -> None:
    """Stateless callers (state=None) get a single fire (ephemeral armed)."""
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="is_below"
    )
    fired = evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None)
    assert len(fired) == 1


def test_rule_fingerprint_stable_and_changes_on_edit() -> None:
    a = PriceBelowAlert(kind="price_below", threshold_usd=200.0, mode="is_below")
    a2 = PriceBelowAlert(kind="price_below", threshold_usd=200.0, mode="is_below")
    assert rule_fingerprint(a) == rule_fingerprint(a2)
    # Editing threshold / condition / mode each yields a new key (re-arms).
    assert rule_fingerprint(a) != rule_fingerprint(
        PriceBelowAlert(kind="price_below", threshold_usd=201.0, mode="is_below")
    )
    assert rule_fingerprint(a) != rule_fingerprint(
        PriceBelowAlert(
            kind="price_below", threshold_usd=200.0, mode="is_below", condition="new"
        )
    )
    assert rule_fingerprint(a) != rule_fingerprint(
        PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    )


def test_alerts_state_json_round_trip_and_tolerates_garbage() -> None:
    s = AlertsState(armed={"k": False, "j": True})
    assert AlertsState.from_json(s.to_json()).armed == {"k": False, "j": True}
    # Garbage / wrong shapes degrade to an empty (all-armed) state.
    assert AlertsState.from_json("nope").armed == {}
    assert AlertsState.from_json({"armed": "nope"}).armed == {}
    assert AlertsState.from_json({"armed": {"x": "notbool"}}).armed == {}


def test_load_save_alerts_state_round_trip(tmp_path: Path) -> None:
    rd = tmp_path / "reports" / "slug"
    rd.mkdir(parents=True)
    assert load_alerts_state("slug", report_dir=rd).armed == {}  # missing file
    save_alerts_state("slug", AlertsState(armed={"fp": False}), report_dir=rd)
    assert load_alerts_state("slug", report_dir=rd).armed == {"fp": False}


def test_is_below_state_persists_across_runs_via_disk(tmp_path: Path) -> None:
    """End-to-end: fire once, persist, reload -> stays silent until re-arm."""
    rd = tmp_path / "reports" / "slug"
    rd.mkdir(parents=True)
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="is_below"
    )
    s1 = load_alerts_state("slug", report_dir=rd)
    assert len(evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None, s1)) == 1
    save_alerts_state("slug", s1, report_dir=rd)
    # Next run reloads the (disarmed) state from disk -> silent.
    s2 = load_alerts_state("slug", report_dir=rd)
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None, s2) == []


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


# ---------------------------------------------------------------------------
# ADR-057 — price_below mode: while_below (stateless, fires every run below)
# ---------------------------------------------------------------------------


def test_while_below_fires_every_run_while_below() -> None:
    """The point of while_below: no per-dip dedupe — it fires on every run
    the cheapest is below, unlike is_below (which fires once per dip)."""
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="while_below"
    )
    state = AlertsState()
    for price in (150.0, 140.0, 199.99):
        fired = evaluate_alerts(
            [rule], [_mk_listing(unit_price_usd=price)], None, state
        )
        assert len(fired) == 1
        assert f"{price:,.2f}" in fired[0].headline
    # Stateless: never touched the armed map.
    assert state.armed == {}


def test_while_below_silent_when_at_or_above_or_no_listing() -> None:
    rule = PriceBelowAlert(
        kind="price_below", threshold_usd=200.0, mode="while_below"
    )
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=200.0)], None) == []
    assert evaluate_alerts([rule], [_mk_listing(unit_price_usd=250.0)], None) == []
    # Ship-simple flake handling: a run with no eligible listing just doesn't
    # fire that run (no state, so it resumes the next run automatically).
    assert evaluate_alerts([rule], [], None) == []
    after = evaluate_alerts([rule], [_mk_listing(unit_price_usd=150.0)], None)
    assert len(after) == 1


def test_while_below_respects_condition_filter() -> None:
    rule = PriceBelowAlert(
        kind="price_below",
        threshold_usd=200.0,
        mode="while_below",
        condition="new",
    )
    used_only = [_mk_listing(unit_price_usd=50.0, condition="used")]
    assert evaluate_alerts([rule], used_only, None) == []
    new_below = [_mk_listing(unit_price_usd=150.0, condition="new")]
    fired = evaluate_alerts([rule], new_below, None)
    assert len(fired) == 1
    assert "(new)" in fired[0].headline


def test_while_below_fingerprint_distinct_from_other_modes() -> None:
    base = dict(kind="price_below", threshold_usd=200.0)
    assert rule_fingerprint(
        PriceBelowAlert(**base, mode="while_below")
    ) not in {
        rule_fingerprint(PriceBelowAlert(**base, mode="is_below")),
        rule_fingerprint(PriceBelowAlert(**base, mode="drops_below")),
    }


# ---------------------------------------------------------------------------
# ADR-059 — price_basis: 'unit' (default) vs 'total' (as-sold / kit price)
# ---------------------------------------------------------------------------


def test_price_basis_defaults_to_unit() -> None:
    rule = PriceBelowAlert(kind="price_below", threshold_usd=200.0)
    assert rule.price_basis == "unit"


def test_total_basis_uses_kit_price_and_rerank() -> None:
    """A 4-module kit at $400 (=$100/unit) vs a single at $150. By unit the
    kit is cheapest ($100); by total the single is ($150 < $400)."""
    kit = _mk_listing(
        unit_price_usd=100.0, is_kit=True, kit_module_count=4, kit_price_usd=400.0
    )
    single = _mk_listing(unit_price_usd=150.0)
    listings = [kit, single]

    unit_rule = PriceBelowAlert(
        kind="price_below", threshold_usd=120.0, mode="while_below"
    )  # default unit: cheapest unit = $100 < $120 -> fires
    fired_unit = evaluate_alerts([unit_rule], listings, None)
    assert len(fired_unit) == 1
    assert "unit price is $100.00" in fired_unit[0].headline

    total_rule = PriceBelowAlert(
        kind="price_below",
        threshold_usd=200.0,
        mode="while_below",
        price_basis="total",
    )  # total: cheapest as-sold = single $150 < $200 -> fires; kit total $400 ignored
    fired_total = evaluate_alerts([total_rule], listings, None)
    assert len(fired_total) == 1
    assert "total price is $150.00" in fired_total[0].headline

    # total basis at $140 must NOT fire (cheapest as-sold is the $150 single;
    # the kit's $400 total is well above) — proves it is not using unit price.
    no_fire = PriceBelowAlert(
        kind="price_below",
        threshold_usd=140.0,
        mode="while_below",
        price_basis="total",
    )
    assert evaluate_alerts([no_fire], listings, None) == []


def test_total_basis_non_kit_falls_back_to_unit_price() -> None:
    """A non-kit listing's as-sold price IS its unit price (kit_price None)."""
    single = _mk_listing(unit_price_usd=180.0)
    rule = PriceBelowAlert(
        kind="price_below",
        threshold_usd=200.0,
        mode="while_below",
        price_basis="total",
    )
    fired = evaluate_alerts([rule], [single], None)
    assert len(fired) == 1
    assert "total price is $180.00" in fired[0].headline


def test_price_basis_fingerprint_distinct() -> None:
    base = dict(kind="price_below", threshold_usd=200.0, mode="is_below")
    assert rule_fingerprint(
        PriceBelowAlert(**base, price_basis="unit")
    ) != rule_fingerprint(PriceBelowAlert(**base, price_basis="total"))
