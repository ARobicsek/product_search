"""Polite coordination for the shared home GPU box (Phase 42 / ADR-147).

The owner's home inference server (``llama-swap``, OpenAI-compatible) generally
runs **one model at a time** and **other people use it**. Sending a request for
``qwen-coder`` forces llama-swap to unload whatever model is currently resident
— rude if someone else is actively using that other model. So before the
``ai_filter`` issues a local request we consult the box and decide whether it's
polite + safe to proceed *now*, to wait, or to give up and fall back to Haiku.

The owner's rule (ADR-147):

* nothing loaded, OR our model already loaded → **join immediately**;
* a DIFFERENT model loaded → do NOT force a swap; **wait until that model has
  had no active inference for ``idle_wait_secs`` (5 min)**, then swap;
* the wait is **bounded** (``max_wait_secs``) → on timeout, **fall back to
  Haiku** so a run never hangs.

Discovered signals on llama-swap v230 (read-only probing, 2026-06-28):

* ``GET /running`` → ``{"running":[{"model","state","ttl",...}]}`` — which
  model(s) are *resident*. Empty ``{"running":[]}`` when idle. **State stays
  ``ready`` during active serving**, so ``/running`` alone tells you *loaded*,
  NOT *busy*.
* ``GET /api/metrics`` → JSON array of every **completed** request, each with a
  ``timestamp`` (RFC3339) + ``model`` + ``duration_ms``. The max timestamp for a
  model is when it last *finished* serving — a real "last inference" signal.
  **Caveat (load-bearing):** it only logs on completion, so a long *in-flight*
  request is invisible here (observed live: a model resident + drawing 229W with
  ``inflight=1`` whose newest ``/api/metrics`` entry was 3.8h old). Last-completed
  alone would wrongly read that as "idle 3.8h → safe to swap".
* ``GET /api/events`` (SSE) → emits an ``inflight`` event ``{"total":N}`` — the
  authoritative count of requests being served *right now*. This is the guard
  that closes the in-flight hole above.
* ``GET /metrics`` (Prometheus) → system/GPU gauges (``gpu_util_percent``,
  ``gpu_power_draw_watts``) — a coarse global "actively computing" proxy used as
  a fallback when the SSE read fails (idle ~11W vs active ~229W).

So idleness is judged from BOTH: last-completed timestamps (credit prior idle
time, so we don't pointlessly wait 5 min for an already-long-idle model) AND a
live "active now" guard (never swap out a model mid-request).

Everything here degrades safely: any probe failure resolves to "fall back to
Haiku", never an exception that breaks a run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from product_search.config import FilterBackendConfig

# Activity thresholds for the GPU fallback proxy (only used when the SSE
# inflight read is unavailable). Idle-resident draws tens of watts; active
# generation drives util up and power past ~200W.
_GPU_UTIL_ACTIVE_PCT = 3.0
_GPU_POWER_ACTIVE_W = 80.0

# Short timeout for the read-only coordination probes — these must never block a
# run. (The actual filter request uses a long timeout for cold loads.)
_PROBE_TIMEOUT_S = 5.0


def _root(base_url: str) -> str:
    """Return the llama-swap management root for an OpenAI ``base_url``.

    The OpenAI-compatible surface is at ``<root>/v1``; the management endpoints
    (``/running``, ``/api/metrics``, ``/api/events``, ``/metrics``) live at the
    root. ``http://host:8080/v1`` → ``http://host:8080``.
    """
    b = base_url.rstrip("/")
    if b.endswith("/v1"):
        b = b[: -len("/v1")]
    return b.rstrip("/")


@dataclass
class BoxSnapshot:
    """A point-in-time read of the box used by the coordinator.

    ``active`` is ``None`` when activity could not be determined (both the SSE
    inflight read and the GPU fallback failed) — distinct from ``False`` (known
    idle). ``last_completed`` maps model id → its newest completed-request time.
    """

    reachable: bool
    loaded: list[str] = field(default_factory=list)
    active: bool | None = None
    last_completed: dict[str, datetime] = field(default_factory=dict)


def _get_running(root: str, timeout: float) -> list[str]:
    resp = httpx.get(f"{root}/running", timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    out: list[str] = []
    for entry in data.get("running", []):
        model = entry.get("model") if isinstance(entry, dict) else None
        if model:
            out.append(str(model))
    return out


def _get_last_completed(root: str, timeout: float) -> dict[str, datetime]:
    """Map model id → newest completed-request timestamp from ``/api/metrics``."""
    resp = httpx.get(f"{root}/api/metrics", timeout=timeout)
    resp.raise_for_status()
    entries = resp.json()
    latest: dict[str, datetime] = {}
    if not isinstance(entries, list):
        return latest
    for e in entries:
        if not isinstance(e, dict):
            continue
        model = e.get("model")
        ts_raw = e.get("timestamp")
        if not model or not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        cur = latest.get(str(model))
        if cur is None or ts > cur:
            latest[str(model)] = ts
    return latest


def _get_inflight(root: str, timeout: float) -> int | None:
    """Return the live in-flight request count from the SSE event stream.

    llama-swap emits an ``inflight`` event on connect, so we read until the
    first one (or the timeout) and return ``total``. ``None`` on any failure.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("GET", f"{root}/api/events") as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        outer = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    if outer.get("type") != "inflight":
                        continue
                    inner = outer.get("data")
                    if isinstance(inner, str):
                        inner = json.loads(inner)
                    if isinstance(inner, dict) and "total" in inner:
                        return int(inner["total"])
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return None


def _get_gpu_active(root: str, timeout: float) -> bool | None:
    """GPU-load fallback for 'active now' when the SSE read is unavailable.

    Parses the Prometheus ``/metrics`` text; active if GPU util or power draw is
    clearly above resting. ``None`` on failure.
    """
    try:
        resp = httpx.get(f"{root}/metrics", timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    util_max = 0.0
    power_max = 0.0
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        try:
            value = float(line.rsplit("}", 1)[-1].strip() if "}" in line else line.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            continue
        if line.startswith("llamaswap_gpu_util_percent"):
            util_max = max(util_max, value)
        elif line.startswith("llamaswap_gpu_power_draw_watts"):
            power_max = max(power_max, value)
    return util_max >= _GPU_UTIL_ACTIVE_PCT or power_max >= _GPU_POWER_ACTIVE_W


def probe_box(base_url: str, *, timeout: float = _PROBE_TIMEOUT_S) -> BoxSnapshot:
    """Read the box's loaded model(s), live activity, and last-completed times.

    Never raises: an unreachable box returns ``BoxSnapshot(reachable=False)`` so
    the coordinator falls back to Haiku.
    """
    root = _root(base_url)
    try:
        loaded = _get_running(root, timeout)
    except (httpx.HTTPError, ValueError, KeyError):
        return BoxSnapshot(reachable=False)

    # Last-completed history is best-effort (used only to credit prior idle time).
    try:
        last_completed = _get_last_completed(root, timeout)
    except (httpx.HTTPError, ValueError, KeyError):
        last_completed = {}

    # Live activity: prefer the authoritative inflight count; fall back to GPU.
    inflight = _get_inflight(root, timeout)
    if inflight is not None:
        active: bool | None = inflight > 0
    else:
        active = _get_gpu_active(root, timeout)

    return BoxSnapshot(
        reachable=True, loaded=loaded, active=active, last_completed=last_completed
    )


def coordinate_local_access(
    cfg: FilterBackendConfig,
    *,
    probe_fn: Callable[[], BoxSnapshot] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Decide whether to run the filter on the local box now (the "plays nice").

    Returns ``True`` → the caller proceeds with the local model. Returns
    ``False`` → the caller falls back to Haiku (box unreachable, or a different
    model stayed busy past ``max_wait_secs``). Blocks (bounded) while a DIFFERENT
    model is in active use, polling every ``poll_secs``.

    All side-effecting collaborators are injectable for tests:
    ``probe_fn`` (the box read), ``now_fn`` (wall clock), ``sleep_fn``, ``log_fn``.
    """
    import time

    _probe = probe_fn or (lambda: probe_box(cfg.local_base))
    _now = now_fn or (lambda: datetime.now(tz=UTC))
    _sleep = sleep_fn or time.sleep
    _log = log_fn or (lambda _m: None)

    start = _now()
    # The most recent moment we KNOW a different loaded model was active. Seeded
    # lazily from /api/metrics the first time we observe it idle, so an already
    # long-idle model is swapped without a pointless 5-min wait.
    last_active: datetime | None = None

    while True:
        snap = _probe()
        now = _now()

        if not snap.reachable:
            _log("local box unreachable -> falling back to Haiku")
            return False

        if (not snap.loaded) or (cfg.local_model in snap.loaded):
            _log(
                "joining local box "
                f"({'idle' if not snap.loaded else cfg.local_model + ' already loaded'})"
            )
            return True

        # A different model is resident.
        other = ", ".join(snap.loaded)
        if snap.active:
            # Actively serving right now — reset the idle clock; never swap.
            last_active = now
        else:
            if last_active is None:
                # First idle observation: credit prior idle time from the
                # loaded model(s)' last completed request, else start from now.
                completed = [
                    snap.last_completed[m]
                    for m in snap.loaded
                    if m in snap.last_completed
                ]
                last_active = max(completed) if completed else now
            idle_secs = (now - last_active).total_seconds()
            if idle_secs >= cfg.idle_wait_secs:
                _log(
                    f"'{other}' idle {idle_secs:.0f}s >= {cfg.idle_wait_secs:.0f}s "
                    f"-> swapping in {cfg.local_model}"
                )
                return True

        waited = (now - start).total_seconds()
        if waited >= cfg.max_wait_secs:
            _log(
                f"'{other}' still busy after {waited:.0f}s "
                f"(max {cfg.max_wait_secs:.0f}s) -> falling back to Haiku"
            )
            return False

        _log(f"'{other}' in use; waiting (waited {waited:.0f}s) ...")
        _sleep(cfg.poll_secs)
