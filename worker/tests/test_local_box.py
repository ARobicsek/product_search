"""Tests for the polite shared-box coordinator (Phase 42 / ADR-147).

The coordinator decides whether the ai_filter may run on the home llama-swap box
*now* without rudely evicting a model someone else is using. It is built from
two one-shot reads (``/running`` + ``/api/metrics``) plus a live "active now"
guard (SSE ``inflight`` / GPU load). These tests inject the box read + clock so
the join / wait / timeout-fallback-to-Haiku branches are exercised without a box.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from product_search.config import FilterBackendConfig
from product_search.llm import local_box
from product_search.llm.local_box import BoxSnapshot, _root, coordinate_local_access


def _cfg(**over: object) -> FilterBackendConfig:
    base = dict(
        backend="local",
        local_base="http://box:8080/v1",
        local_model="qwen-coder",
        local_key="dummy",
        local_fallback_model="qwen3.6-27b-mtp",
        idle_wait_secs=30.0,
        max_wait_secs=60.0,
        poll_secs=15.0,
        allow_haiku_fallback=True,
    )
    base.update(over)
    return FilterBackendConfig(**base)  # type: ignore[arg-type]


class _Clock:
    """Wall clock that only advances when ``sleep`` is called (no real waiting)."""

    def __init__(self, start: datetime) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, secs: float) -> None:
        self.sleeps.append(secs)
        self.t += timedelta(seconds=secs)


def _run(cfg: FilterBackendConfig, snaps, clock: _Clock):
    """Drive the coordinator with a scripted snapshot source."""
    seq = list(snaps)

    def probe() -> BoxSnapshot:
        # Repeat the last snapshot once the script is exhausted.
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return coordinate_local_access(
        cfg, probe_fn=probe, now_fn=clock.now, sleep_fn=clock.sleep
    )


# --- join-immediately branches ---------------------------------------------


def test_unreachable_box_falls_back_to_haiku() -> None:
    clock = _Clock(datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    assert _run(_cfg(), [BoxSnapshot(reachable=False)], clock) is False
    assert clock.sleeps == []  # never waits


def test_idle_box_joins_immediately() -> None:
    clock = _Clock(datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    assert _run(_cfg(), [BoxSnapshot(reachable=True, loaded=[])], clock) is True
    assert clock.sleeps == []


def test_our_model_already_loaded_joins_immediately() -> None:
    clock = _Clock(datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    snap = BoxSnapshot(reachable=True, loaded=["qwen-coder"], active=True)
    # Even if qwen-coder is actively serving (someone else is using it), we join.
    assert _run(_cfg(), [snap], clock) is True
    assert clock.sleeps == []


# --- different-model-loaded branches ---------------------------------------


def test_different_model_long_idle_swaps_immediately() -> None:
    """A different model, idle > 5 min (credited from /api/metrics), swaps now."""
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    clock = _Clock(now)
    snap = BoxSnapshot(
        reachable=True,
        loaded=["minimax-m3"],
        active=False,
        last_completed={"minimax-m3": now - timedelta(minutes=10)},
    )
    assert _run(_cfg(), [snap], clock) is True
    assert clock.sleeps == []  # no pointless wait — it was already idle


def test_different_model_busy_then_idle_waits_then_swaps() -> None:
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    clock = _Clock(now)
    busy = BoxSnapshot(reachable=True, loaded=["minimax-m3"], active=True)
    idle = BoxSnapshot(reachable=True, loaded=["minimax-m3"], active=False)
    # busy@t0 (last_active=t0) → sleep → idle@t15 (idle=15<30) → sleep →
    # idle@t30 (idle=30>=30) → swap.
    assert _run(_cfg(), [busy, idle, idle], clock) is True
    assert clock.sleeps == [15.0, 15.0]


def test_different_model_stays_busy_falls_back_to_haiku() -> None:
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    clock = _Clock(now)
    busy = BoxSnapshot(reachable=True, loaded=["minimax-m3"], active=True)
    # Always busy → idle never accrues → bounded by max_wait (60s) → Haiku.
    assert _run(_cfg(), [busy], clock) is False
    # waited 0,15,30,45 then 60>=max → stop. 4 sleeps max; bounded (never hangs).
    assert clock.sleeps == [15.0, 15.0, 15.0, 15.0]


def test_inflight_guard_does_not_trust_stale_completed_metric() -> None:
    """The load-bearing case: a model resident + actively serving but whose newest
    /api/metrics entry is hours old (metrics log on completion). last-completed
    alone would read "idle hours → swap"; the live ``active`` guard must prevent it.
    """
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    clock = _Clock(now)
    snap = BoxSnapshot(
        reachable=True,
        loaded=["minimax-m3-q4-fast"],
        active=True,  # inflight>0 right now
        last_completed={"minimax-m3-q4-fast": now - timedelta(hours=3, minutes=48)},
    )
    # Must NOT swap despite the 3.8h-stale completed metric → bounded → Haiku.
    assert _run(_cfg(), [snap], clock) is False
    assert clock.sleeps  # it waited rather than rudely swapping


def test_unknown_activity_seeds_idle_clock_from_now() -> None:
    """A different model, not active, with no completed-metric record: we can't
    credit prior idle, so the clock starts now and we wait the full idle window."""
    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    clock = _Clock(now)
    snap = BoxSnapshot(reachable=True, loaded=["mystery"], active=False, last_completed={})
    # idle_wait=30, poll=15: t0 seed→idle 0; t15 idle 15; t30 idle 30→swap.
    assert _run(_cfg(), [snap], clock) is True
    assert clock.sleeps == [15.0, 15.0]


# --- _root URL derivation ---------------------------------------------------


def test_root_strips_v1_suffix() -> None:
    assert _root("http://100.68.68.101:8080/v1") == "http://100.68.68.101:8080"
    assert _root("http://host:8080/v1/") == "http://host:8080"
    assert _root("http://host:8080") == "http://host:8080"


# --- probe_box parsing (monkeypatched httpx) --------------------------------


class _FakeResp:
    def __init__(self, *, json_data=None, text: str = "", lines=None) -> None:
        self._json = json_data
        self.text = text
        self._lines = lines or []

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method: str, url: str):
        # SSE snapshot-on-connect: a logData line then an inflight event.
        return _FakeResp(
            lines=[
                'data:{"type":"logData","data":"noise"}',
                'data:{"type":"inflight","data":"{\\"total\\":1}"}',
            ]
        )


def test_probe_box_parses_running_metrics_and_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = {"running": [{"model": "minimax-m3", "state": "ready"}]}
    metrics = [
        {"model": "minimax-m3", "timestamp": "2026-06-28T08:00:00Z"},
        {"model": "minimax-m3", "timestamp": "2026-06-28T08:30:00Z"},
        {"model": "qwen-coder", "timestamp": "2026-06-28T07:00:00Z"},
    ]

    def fake_get(url: str, timeout: float = 5.0):
        if url.endswith("/running"):
            return _FakeResp(json_data=running)
        if url.endswith("/api/metrics"):
            return _FakeResp(json_data=metrics)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(local_box.httpx, "get", fake_get)
    monkeypatch.setattr(local_box.httpx, "Client", _FakeClient)

    snap = local_box.probe_box("http://box:8080/v1")
    assert snap.reachable is True
    assert snap.loaded == ["minimax-m3"]
    assert snap.active is True  # inflight total=1
    # newest per model
    assert snap.last_completed["minimax-m3"] == datetime(2026, 6, 28, 8, 30, tzinfo=UTC)
    assert snap.last_completed["qwen-coder"] == datetime(2026, 6, 28, 7, 0, tzinfo=UTC)


def test_probe_box_unreachable_when_running_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str, timeout: float = 5.0):
        raise httpx.ConnectError("box down")

    monkeypatch.setattr(local_box.httpx, "get", boom)
    snap = local_box.probe_box("http://box:8080/v1")
    assert snap.reachable is False
