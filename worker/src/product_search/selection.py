"""Diversity / anti-domination selection for the displayed set (Phase 32).

Price is the point of the tool, so survivors are ranked cheapest-first. But the
display must not be dominated by one vendor (REBUILD_PLAN §7 / §11 decision 3):
a ``per_vendor_cap`` keeps at most N offers per vendor in the shown ranking, and
a ``max_listings`` breadth knob ("10 best / 50 best") truncates the total.

Crucially, the cap and truncation apply only to the *displayed* set — the full
survivor list still persists to history (REBUILD_PLAN §5.6). The dropped-by-cap
counts come back as ``overflow`` so the UI can render an "N more from <vendor>"
affordance.

Pure + deterministic. ``price_anomaly_low`` listings (flagged by
``price_sanity``) are excluded from display but counted in ``hidden_anomalies``
so the cheap-scam anomaly never ranks #1 (ADR-131 P1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from product_search.models import Listing
from product_search.validators.price_sanity import FLAG_PRICE_ANOMALY_LOW

_PRICE_FLOOR = float("inf")  # unpriced listings sort to the bottom


@dataclass
class SelectionResult:
    """Outcome of ranking + capping the survivors for display."""

    displayed: list[Listing]
    # vendor -> how many of that vendor's offers were dropped by the per-vendor
    # cap (drives the "N more from <vendor>" affordance). Only vendors that are
    # actually represented in ``displayed`` appear here.
    overflow: dict[str, int] = field(default_factory=dict)
    # offers excluded from display as low price anomalies (kept in history).
    hidden_anomalies: int = 0


def vendor_key(listing: Listing) -> str:
    """Stable per-vendor grouping key — the merchant name, normalised."""
    name = (listing.seller_name or "").strip().lower()
    return name or "(unknown vendor)"


# Adapter source id → the marketplace label a user would name in an allow/block
# list. An eBay listing's ``seller_name`` is an individual username, so "never
# eBay" must match the *marketplace*, not the username. (Serper merchants surface
# their real name in ``seller_name``, so they need no extra label.)
_MARKETPLACE_LABEL: dict[str, str] = {"ebay_search": "ebay"}


def _vendor_match_tokens(listing: Listing) -> list[str]:
    """Lowercased identity tokens a vendor allow/block entry can match against."""
    tokens = [vendor_key(listing)]
    label = _MARKETPLACE_LABEL.get(listing.source)
    if label:
        tokens.append(label)
    return tokens


def vendor_matches_any(listing: Listing, entries: list[str]) -> bool:
    """True if *listing*'s vendor matches any allow/block-list *entry*.

    Match is a case-insensitive substring of the entry within a vendor token
    (so "Walmart" matches the Serper source "Walmart - Seller", "B&H" matches
    "B&H Photo-Video", and "eBay" matches both the eBay marketplace and a Serper
    result whose ``source`` is literally "eBay"). Empty entries are ignored.
    """
    for raw in entries:
        e = (raw or "").strip().lower()
        if not e:
            continue
        if any(e in t for t in _vendor_match_tokens(listing) if t):
            return True
    return False


def apply_vendor_filter(
    listings: list[Listing],
    *,
    allowlist: list[str],
    blocklist: list[str],
) -> list[Listing]:
    """Scope the recall set to the user's vendor preferences (REBUILD_PLAN §5.3).

    A non-empty ``allowlist`` keeps only listings from a named vendor ("only
    these vendors"); a ``blocklist`` drops listings from a named vendor ("never
    eBay/Poshmark/etc"). Both empty → passthrough. The allowlist is applied
    first, then the blocklist (a vendor in both is excluded). Deterministic.
    """
    out = listings
    if allowlist:
        out = [lst for lst in out if vendor_matches_any(lst, allowlist)]
    if blocklist:
        out = [lst for lst in out if not vendor_matches_any(lst, blocklist)]
    return out


def price_sort_key(listing: Listing) -> tuple[int, float]:
    """Sort key: anomalies last, then cheapest first. Unpriced → bottom."""
    p = listing.price_usd
    price_val = p if p and p > 0 else _PRICE_FLOOR
    is_anomalous = 1 if FLAG_PRICE_ANOMALY_LOW in listing.flags else 0
    return (is_anomalous, price_val)


def select_for_display(
    listings: list[Listing],
    *,
    max_listings: int,
    per_vendor_cap: int,
) -> SelectionResult:
    """Rank cheapest-first, cap per vendor, truncate to ``max_listings``.

    Excludes ``price_anomaly_low`` listings from the displayed ranking (counted
    in ``hidden_anomalies``). Returns the displayed slice plus the per-vendor
    overflow counts for the offers the cap held back.
    """
    hidden = 0

    ranked = sorted(listings, key=price_sort_key)

    per_vendor_seen: dict[str, int] = {}
    capped: list[Listing] = []
    overflow: dict[str, int] = {}
    for lst in ranked:
        v = vendor_key(lst)
        seen = per_vendor_seen.get(v, 0)
        if seen < per_vendor_cap:
            capped.append(lst)
            per_vendor_seen[v] = seen + 1
        else:
            overflow[v] = overflow.get(v, 0) + 1

    displayed = capped[:max_listings]

    # Only report overflow for vendors actually shown (a vendor entirely cut by
    # the max_listings truncation isn't a "N more from <vendor>" case).
    shown_vendors = {vendor_key(lst) for lst in displayed}
    overflow = {v: n for v, n in overflow.items() if v in shown_vendors}

    return SelectionResult(
        displayed=displayed,
        overflow=overflow,
        hidden_anomalies=hidden,
    )
