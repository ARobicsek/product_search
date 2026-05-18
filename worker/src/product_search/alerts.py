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

import json
from dataclasses import dataclass, field
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


@dataclass
class AlertsState:
    """Per-rule firing state that must survive between runs (ADR-056).

    ``armed[fingerprint]`` is ``True`` when an ``is_below`` rule is eligible to
    fire on its next below-threshold observation. A missing key means "armed"
    (a freshly created or edited rule starts armed, so it fires immediately if
    the price is already below). ``drops_below``, ``while_below`` and
    ``vendor_seen`` rules are stateless and never touch this.
    """

    armed: dict[str, bool] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {"armed": self.armed}

    @classmethod
    def from_json(cls, raw: object) -> AlertsState:
        if not isinstance(raw, dict):
            return cls()
        armed_raw = raw.get("armed")
        if not isinstance(armed_raw, dict):
            return cls()
        armed = {
            str(k): bool(v)
            for k, v in armed_raw.items()
            if isinstance(v, bool)
        }
        return cls(armed=armed)


def rule_fingerprint(rule: AlertRule) -> str:
    """Stable per-rule key for state. Editing any salient field (threshold,
    condition, mode) changes the fingerprint, which re-arms the rule — an
    edited rule behaving like a new one is the intended behavior."""
    if isinstance(rule, PriceBelowAlert):
        return (
            f"price_below|{rule.mode}|{rule.threshold_usd}"
            f"|{rule.condition or ''}|{rule.price_basis}"
        )
    if isinstance(rule, VendorSeenAlert):
        return f"vendor_seen|{rule.host}"
    return repr(rule)  # pragma: no cover — discriminator rejects unknown kinds


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


def _effective_price(listing: Listing, basis: str) -> float:
    """The price an alert threshold compares against (ADR-059).

    ``total`` = the listing's as-sold price: ``kit_price_usd`` for a kit, else
    the single-unit price (a non-kit's as-sold price *is* its unit price).
    ``unit`` (default) = ``unit_price_usd`` (price of one module).
    """
    if basis == "total" and listing.is_kit and listing.kit_price_usd is not None:
        return listing.kit_price_usd
    return listing.unit_price_usd


def _basis_word(basis: str) -> str:
    return "total" if basis == "total" else "unit"


def _cheapest(
    listings: list[Listing], *, condition: str | None, basis: str = "unit"
) -> Listing | None:
    eligible = [
        lst for lst in listings if condition is None or lst.condition == condition
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda lst: _effective_price(lst, basis))


def _evaluate_price_below(
    rule: PriceBelowAlert,
    current: list[Listing],
    previous: list[Listing] | None,
) -> FiredAlert | None:
    basis = rule.price_basis
    curr = _cheapest(current, condition=rule.condition, basis=basis)
    if curr is None or _effective_price(curr, basis) >= rule.threshold_usd:
        return None
    if previous is not None:
        prev = _cheapest(previous, condition=rule.condition, basis=basis)
        if prev is not None and _effective_price(prev, basis) < rule.threshold_usd:
            # Already below the threshold last run — not a transition.
            return None
    cond_phrase = f" ({rule.condition})" if rule.condition else ""
    headline = (
        f"Cheapest{cond_phrase} {_basis_word(basis)} price dropped to "
        f"${_effective_price(curr, basis):,.2f} "
        f"— alert threshold ${rule.threshold_usd:,.2f}"
    )
    return FiredAlert(rule=rule, headline=headline)


def _evaluate_price_is_below(
    rule: PriceBelowAlert,
    current: list[Listing],
    state: AlertsState,
) -> FiredAlert | None:
    """State-based ``is_below`` (ADR-056): fire whenever the matching cheapest
    is below the threshold AND the rule is armed; then disarm. Re-arm whenever
    the matching cheapest is not below (>= threshold, or no eligible listing).
    A missing fingerprint = armed, so a newly created/edited rule fires on its
    first below observation even if the price was already below.
    """
    basis = rule.price_basis
    fp = rule_fingerprint(rule)
    armed = state.armed.get(fp, True)
    curr = _cheapest(current, condition=rule.condition, basis=basis)
    is_below = (
        curr is not None and _effective_price(curr, basis) < rule.threshold_usd
    )
    if not is_below:
        state.armed[fp] = True  # re-arm for the next dip
        return None
    if not armed:
        return None  # already alerted for this dip
    state.armed[fp] = False
    assert curr is not None  # is_below implies curr is not None
    cond_phrase = f" ({rule.condition})" if rule.condition else ""
    headline = (
        f"Cheapest{cond_phrase} {_basis_word(basis)} price is "
        f"${_effective_price(curr, basis):,.2f} "
        f"— at or below your ${rule.threshold_usd:,.2f} alert"
    )
    return FiredAlert(rule=rule, headline=headline)


def _evaluate_price_while_below(
    rule: PriceBelowAlert,
    current: list[Listing],
) -> FiredAlert | None:
    """State-free ``while_below`` (ADR-057): fire on every run where the
    matching cheapest is below the threshold — no per-dip dedupe, no armed
    flag. A run with no eligible listing simply does not fire (ship-simple;
    the robust "N sources errored" handling is the deferred ADR-053 item)."""
    basis = rule.price_basis
    curr = _cheapest(current, condition=rule.condition, basis=basis)
    if curr is None or _effective_price(curr, basis) >= rule.threshold_usd:
        return None
    cond_phrase = f" ({rule.condition})" if rule.condition else ""
    headline = (
        f"Cheapest{cond_phrase} {_basis_word(basis)} price is "
        f"${_effective_price(curr, basis):,.2f} "
        f"— at or below your ${rule.threshold_usd:,.2f} alert"
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
    state: AlertsState | None = None,
) -> list[FiredAlert]:
    """Evaluate every rule and return the ones that fire on this run.

    ``previous=None`` means "no prior run on disk"; transition-style rules
    (``drops_below``, ``vendor_seen``) treat that as "first observation
    counts". ``is_below`` price rules consult/mutate ``state`` instead of
    ``previous``; ``state`` is mutated in place (arm/disarm) so the caller can
    persist it. When ``state`` is ``None`` an ephemeral one is used (every
    ``is_below`` rule is treated as armed — only correct for one-shot callers
    that do not persist; the production path always passes a loaded state).
    """
    if state is None:
        state = AlertsState()
    fired: list[FiredAlert] = []
    for rule in rules:
        if isinstance(rule, PriceBelowAlert):
            if rule.mode == "is_below":
                res = _evaluate_price_is_below(rule, current, state)
            elif rule.mode == "while_below":
                res = _evaluate_price_while_below(rule, current)
            else:
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


def alerts_state_path(slug: str, *, report_dir: Path | None = None) -> Path:
    """``reports/<slug>/alerts_state.json`` — sibling of the ``data/`` dir.

    ``report_dir`` overrides the default location for tests. Committed
    automatically by the scheduled workflow's ``git add -A`` step alongside
    the run's CSV/report (no workflow change needed)."""
    if report_dir is None:
        from product_search.storage.db import _repo_root

        report_dir = _repo_root() / "reports" / slug
    return report_dir / "alerts_state.json"


def load_alerts_state(slug: str, *, report_dir: Path | None = None) -> AlertsState:
    """Read persisted per-rule arm state, or an empty (all-armed) state."""
    path = alerts_state_path(slug, report_dir=report_dir)
    if not path.exists():
        return AlertsState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AlertsState()
    return AlertsState.from_json(raw)


def save_alerts_state(
    slug: str, state: AlertsState, *, report_dir: Path | None = None
) -> None:
    path = alerts_state_path(slug, report_dir=report_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
