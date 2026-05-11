"""Phase 17 Part C — post-run alerts evaluator.

User-configured alert rules from ``profile.alerts`` are evaluated *after* the
synthesizer produces the day's ranked listings. Each rule fires at most once
per **transition**: ``price_below`` fires only when the current run's cheapest
matching listing crosses below threshold AND the previous run's cheapest was
at or above (or no previous run exists). ``vendor_seen`` fires only when the
current run has ≥1 passing listing for a host AND the previous run had 0
(or no previous run). Transition semantics are deliberate per the Phase 17
brief in PHASES.md — the alternative ("fire on every matching run") would
spam the user with the same notification daily until the condition cleared.

The previous run is loaded from the most recent CSV under
``reports/<slug>/data/`` (excluding the run's own CSV, which has already been
written by the time alerts evaluate). When there is no prior CSV, ``previous``
is ``None`` and any currently-true rule fires — first observation counts
as a transition.

Notification delivery is delegated to :func:`product_search.notify.notify_material_change`,
which already wraps the Bearer-authed POST to ``/api/push/notify``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from product_search.models import Listing
from product_search.profile import AlertRule, PriceBelowAlert, VendorSeenAlert
from product_search.storage.csv_dump import read_snapshot_csv


@dataclass(frozen=True)
class FiredAlert:
    """One rule that fired during this run, with a human-readable headline
    suitable for the push notification body and the run-output audit panel."""

    rule: AlertRule
    headline: str


def _canonical_host(raw: str | None) -> str | None:
    if not raw:
        return None
    host = raw.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def listing_host(listing: Listing) -> str | None:
    """Canonical vendor host for a listing.

    universal_ai listings carry the host explicitly in ``attrs.vendor_host``
    (set by the adapter at emit time); other adapters embed the vendor in
    ``listing.url`` so we derive from there. Returns lowercase, www-stripped.
    """
    attrs_host = (listing.attrs or {}).get("vendor_host") if listing.attrs else None
    if isinstance(attrs_host, str):
        canon = _canonical_host(attrs_host)
        if canon:
            return canon
    return _canonical_host(urlparse(listing.url).netloc)


def _cheapest(listings: list[Listing], *, condition: str | None) -> Listing | None:
    eligible = [
        lst for lst in listings if condition is None or lst.condition == condition
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda lst: lst.unit_price_usd)


def _evaluate_price_below(
    rule: PriceBelowAlert,
    current: list[Listing],
    previous: list[Listing] | None,
) -> FiredAlert | None:
    curr = _cheapest(current, condition=rule.condition)
    if curr is None or curr.unit_price_usd >= rule.threshold_usd:
        return None
    if previous is not None:
        prev = _cheapest(previous, condition=rule.condition)
        if prev is not None and prev.unit_price_usd < rule.threshold_usd:
            # Already below the threshold last run — not a transition.
            return None
    cond_phrase = f" ({rule.condition})" if rule.condition else ""
    headline = (
        f"Cheapest{cond_phrase} dropped to ${curr.unit_price_usd:,.2f} "
        f"— alert threshold ${rule.threshold_usd:,.2f}"
    )
    return FiredAlert(rule=rule, headline=headline)


def _evaluate_vendor_seen(
    rule: VendorSeenAlert,
    current: list[Listing],
    previous: list[Listing] | None,
) -> FiredAlert | None:
    target = _canonical_host(rule.host)
    if target is None:
        return None
    curr_match = any(listing_host(lst) == target for lst in current)
    if not curr_match:
        return None
    if previous is not None:
        prev_match = any(listing_host(lst) == target for lst in previous)
        if prev_match:
            return None
    headline = f"{target} now has at least one passing listing"
    return FiredAlert(rule=rule, headline=headline)


def evaluate_alerts(
    rules: list[AlertRule],
    current: list[Listing],
    previous: list[Listing] | None,
) -> list[FiredAlert]:
    """Evaluate every rule and return the ones that fire on this transition.

    ``previous=None`` means "no prior run on disk"; under that condition every
    rule whose current state is true fires (first observation counts).
    """
    fired: list[FiredAlert] = []
    for rule in rules:
        if isinstance(rule, PriceBelowAlert):
            res = _evaluate_price_below(rule, current, previous)
        elif isinstance(rule, VendorSeenAlert):
            res = _evaluate_vendor_seen(rule, current, previous)
        else:  # pragma: no cover — schema discriminator rejects unknown kinds
            continue
        if res is not None:
            fired.append(res)
    return fired


def previous_run_csv(
    slug: str, *, exclude: Path | None = None, data_dir: Path | None = None
) -> Path | None:
    """Most recent CSV under ``reports/<slug>/data/`` excluding ``exclude``.

    ``data_dir`` overrides the default location — useful for tests so we
    don't need to monkey-patch the repo-root resolver. Returns ``None`` when
    no prior CSV exists.
    """
    if data_dir is None:
        from product_search.storage.db import _repo_root

        data_dir = _repo_root() / "reports" / slug / "data"
    if not data_dir.exists():
        return None
    excl = exclude.resolve() if exclude is not None else None
    candidates = [
        p
        for p in data_dir.glob("*.csv")
        if excl is None or p.resolve() != excl
    ]
    if not candidates:
        return None
    # Filenames are ``<YYYY-MM-DDTHH-MM-SSZ>.csv`` — lexical sort matches
    # chronological sort because the timestamp is zero-padded UTC.
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0]


def load_previous_run(
    slug: str, *, exclude: Path | None = None, data_dir: Path | None = None
) -> list[Listing] | None:
    """Read the previous run's listings from disk, or ``None`` if no prior run."""
    prev = previous_run_csv(slug, exclude=exclude, data_dir=data_dir)
    if prev is None:
        return None
    return read_snapshot_csv(prev)


def render_audit_panel(fired: list[FiredAlert], outcomes: list[bool]) -> str:
    """Render the run-report 'Alerts fired' markdown panel.

    Mirrors the existing run-cost panel pattern: a heading followed by a
    table-free bullet list. ``outcomes[i]`` is ``True`` when the i-th fired
    alert's notify POST returned success. Returns "" when nothing fired so
    callers can unconditionally append.
    """
    if not fired:
        return ""
    lines = ["## Alerts fired", ""]
    for fa, ok in zip(fired, outcomes):
        status = "ok" if ok else "failed"
        lines.append(f"- **{fa.rule.kind}**: {fa.headline} _(notify={status})_")
    return "\n".join(lines)
