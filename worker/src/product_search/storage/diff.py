"""Pure-Python diff between two daily snapshots.

Given the listings observed on day N-1 and day N (each a flat list of
``Listing``), report:

- ``new``: URLs present in N but not in N-1.
- ``dropped``: URLs present in N-1 but not in N.
- ``changed``: URLs present in both whose ``unit_price_usd`` moved by
  ≥5% in either direction.

The 5% threshold matches the "material change" threshold in the
project plan (see PHASES.md Phase 4 and Phase 11). It's encoded as a
constant so callers/tests can override it.

We compare on ``unit_price_usd`` because it is the only price field
guaranteed non-null on every passing listing. ``total_for_target_usd``
may be ``None`` when no profile configuration matches the listing's
capacity, which would mask real per-listing price moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from product_search.models import Listing

DEFAULT_PRICE_CHANGE_THRESHOLD: float = 0.05  # 5%


@dataclass
class PriceChange:
    """A listing whose price moved between two snapshots."""

    url: str
    title: str
    old_price_usd: float
    new_price_usd: float
    pct_change: float          # signed; positive = price went up
    new_listing: Listing       # the listing as observed today

    @property
    def direction(self) -> str:
        return "up" if self.pct_change > 0 else "down"


@dataclass
class DiffResult:
    """The diff between two daily snapshots."""

    new: list[Listing] = field(default_factory=list)
    dropped: list[Listing] = field(default_factory=list)
    changed: list[PriceChange] = field(default_factory=list)

    @property
    def is_material(self) -> bool:
        """True if any of the three sets is non-empty."""
        return bool(self.new or self.dropped or self.changed)


def diff_snapshots(
    previous: list[Listing],
    current: list[Listing],
    *,
    price_change_threshold: float = DEFAULT_PRICE_CHANGE_THRESHOLD,
) -> DiffResult:
    """Compute the diff. Inputs may be in any order; output is URL-sorted."""
    prev_by_url = {lst.url: lst for lst in previous}
    curr_by_url = {lst.url: lst for lst in current}

    new_urls = sorted(curr_by_url.keys() - prev_by_url.keys())
    dropped_urls = sorted(prev_by_url.keys() - curr_by_url.keys())
    common_urls = sorted(prev_by_url.keys() & curr_by_url.keys())

    changed: list[PriceChange] = []
    for url in common_urls:
        prev = prev_by_url[url]
        curr = curr_by_url[url]
        old = prev.unit_price_usd
        new = curr.unit_price_usd
        if old <= 0:
            # Defensive: avoid div-by-zero; treat any non-zero new as "changed".
            if new != old:
                changed.append(
                    PriceChange(
                        url=url,
                        title=curr.title,
                        old_price_usd=old,
                        new_price_usd=new,
                        pct_change=float("inf") if new > 0 else 0.0,
                        new_listing=curr,
                    )
                )
            continue
        pct = (new - old) / old
        if abs(pct) >= price_change_threshold:
            changed.append(
                PriceChange(
                    url=url,
                    title=curr.title,
                    old_price_usd=old,
                    new_price_usd=new,
                    pct_change=pct,
                    new_listing=curr,
                )
            )

    return DiffResult(
        new=[curr_by_url[u] for u in new_urls],
        dropped=[prev_by_url[u] for u in dropped_urls],
        changed=changed,
    )
