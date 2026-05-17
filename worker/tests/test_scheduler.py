"""Tests for the scheduler-tick due-logic and one-time self-clear.

Covers the Phase-17-follow-up rewrite: minute-aware cron matching with a
look-back window, the Vixie day-of-month / day-of-week OR rule, and the
``schedule:`` block strip mirrored from ``web/lib/schedule.ts``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from product_search.cli import (
    _cron_due,
    _cron_fires_at,
    _expand_cron_field,
    _strip_schedule_block,
)


def test_expand_cron_field_basic() -> None:
    assert _expand_cron_field("*/15", 0, 59) == {0, 15, 30, 45}
    assert _expand_cron_field("8", 0, 23) == {8}
    assert _expand_cron_field("1-5", 0, 6) == {1, 2, 3, 4, 5}
    assert _expand_cron_field("0,30", 0, 59) == {0, 30}
    assert _expand_cron_field("*", 0, 3) == {0, 1, 2, 3}
    # Unsupported / out-of-range → None (conservative: never fire).
    assert _expand_cron_field("bad", 0, 59) is None
    assert _expand_cron_field("99", 0, 59) is None


def test_cron_fires_at_minute_precision() -> None:
    """The old scheduler ignored the minute field; the new one must not."""
    t = datetime(2026, 5, 17, 13, 30, tzinfo=UTC)
    assert _cron_fires_at("30 13 * * *", t) is True
    assert _cron_fires_at("31 13 * * *", t) is False
    assert _cron_fires_at("30 14 * * *", t) is False


def test_cron_fires_at_day_of_week() -> None:
    t = datetime(2026, 5, 17, 8, 0, tzinfo=UTC)
    cron_dow = t.isoweekday() % 7  # cron scheme: Sun=0..Sat=6
    assert _cron_fires_at(f"0 8 * * {cron_dow}", t) is True
    assert _cron_fires_at(f"0 8 * * {(cron_dow + 1) % 7}", t) is False
    # 7 is an accepted alias for Sunday (0).
    sunday = datetime(2026, 5, 17, 8, 0, tzinfo=UTC)
    while sunday.isoweekday() != 7:
        sunday = sunday.replace(day=sunday.day + 1)
    assert _cron_fires_at("0 8 * * 7", sunday) is True


def test_cron_vixie_dom_dow_or_rule() -> None:
    """When both dom and dow are restricted, EITHER matching fires."""
    # 2026-05-18 is a Monday (isoweekday 1). dom=1 (not the 18th) but
    # dow=1 (Monday) → fires by the OR rule.
    monday_18th = datetime(2026, 5, 18, 8, 0, tzinfo=UTC)
    assert monday_18th.isoweekday() == 1
    assert _cron_fires_at("0 8 1 * 1", monday_18th) is True
    # dom-only restricted → must be day 1, AND-style.
    assert _cron_fires_at("0 8 1 * *", monday_18th) is False


def test_cron_due_window() -> None:
    """A firing inside (window_start, now] is due; one already passed is not."""
    cron = "0 8 * * *"  # 08:00 UTC daily
    # now 08:05, window back to 07:50 → 08:00 is in the window.
    now = datetime(2026, 5, 17, 8, 5, tzinfo=UTC)
    assert _cron_due(cron, now.replace(minute=50, hour=7), now) is True
    # now 08:20, window back to 08:05 → 08:00 already passed, not due.
    now2 = datetime(2026, 5, 17, 8, 20, tzinfo=UTC)
    assert _cron_due(cron, now2.replace(minute=5), now2) is False
    # Exactly on the minute (now == firing) counts.
    now3 = datetime(2026, 5, 17, 8, 0, tzinfo=UTC)
    assert _cron_due(cron, now3.replace(minute=45, hour=7), now3) is True


def test_strip_schedule_block_lf_and_crlf() -> None:
    base = (
        "slug: x\n"
        "display_name: X\n"
    )
    with_sched = base + "schedule:\n  cron: 0 8 * * *\n  timezone: UTC\n"
    assert _strip_schedule_block(with_sched) == base
    # One-time block strips too.
    with_once = base + "schedule:\n  run_at: 2026-05-17T12:30:00Z\n  timezone: UTC\n"
    assert _strip_schedule_block(with_once) == base
    # CRLF line endings (committed profiles use them on Windows).
    crlf = base.replace("\n", "\r\n") + (
        "schedule:\r\n  cron: 0 8 * * *\r\n  timezone: UTC\r\n"
    )
    assert _strip_schedule_block(crlf) == base.replace("\n", "\r\n")
    # No schedule block → unchanged.
    assert _strip_schedule_block(base) == base
