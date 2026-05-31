"""Deterministic price-sanity + ship-from gate (Phase 32, ADR-131 P1).

Serper recall is title-only with no detail page to self-correct, so two classes
of bad offer survive the relevance filter and would otherwise rank #1:

* a wildly-underpriced anomaly — e.g. a $67.20 listing titled "DJI Neo 2 Fly
  More Combo" sitting among ~$600 genuine offers (an accessory, a scam, or a
  mis-scoped catalog row). Cheapest-first ranking puts it at the top.
* a foreign / currency-converted offer shipping from outside the wanted market.

Both are caught **deterministically** — no LLM, no fabricated data (ADR-001).
This module only *annotates* ``Listing.flags`` (and, for the ship-from gate,
optionally drops). It never invents a price or a country: a listing with no
price or no ``ship_from_country`` is left untouched.

The price-anomaly check runs over the **matched survivors** (post ai_filter),
not the raw recall set: MAD-vs-median is only meaningful across same-product
offers — over the noisy pre-filter recall (accessories, wrong models) the median
is junk. This is a deliberate refinement of REBUILD_PLAN §5.3's step ordering;
see ADR for the rationale.
"""

from __future__ import annotations

import statistics

from product_search.models import Listing

# Flag strings written to ``Listing.flags``. ``price_anomaly_low`` is the one
# selection.py excludes from the displayed ranking (kept in history); the high
# variant is informational (an expensive outlier never ranks #1 anyway).
FLAG_PRICE_ANOMALY_LOW = "price_anomaly_low"
FLAG_PRICE_ANOMALY_HIGH = "price_anomaly_high"

# Tuning. A listing must clear BOTH a robust MAD threshold AND a relative floor
# to be flagged, so a genuine "20% cheaper than the pack" deal is never hidden —
# only egregious (>50% below / >3x above median) MAD-extreme outliers are.
_DEFAULT_MAD_K = 3.5
_DEFAULT_LOW_RATIO = 0.5     # below median * this → candidate low anomaly
_DEFAULT_HIGH_RATIO = 3.0    # above median * this → candidate high anomaly
# Need a few priced offers before a median/MAD means anything.
_MIN_SAMPLE = 4


def _priced(listings: list[Listing]) -> list[Listing]:
    return [lst for lst in listings if lst.price_usd and lst.price_usd > 0]


def annotate_price_anomalies(
    listings: list[Listing],
    *,
    mad_k: float = _DEFAULT_MAD_K,
    low_ratio: float = _DEFAULT_LOW_RATIO,
    high_ratio: float = _DEFAULT_HIGH_RATIO,
    min_sample: int = _MIN_SAMPLE,
) -> int:
    """Flag price outliers in place; return the number of LOW anomalies flagged.

    A listing is flagged ``price_anomaly_low`` when its price is both
    ``< median * low_ratio`` **and** more than ``mad_k`` MADs below the median
    (or, when MAD is 0 because prices cluster, just the ratio test). The high
    variant is symmetric. Listings with no usable price are never flagged.

    No-op (returns 0) when fewer than ``min_sample`` priced listings exist —
    too few points to call anything an outlier.
    """
    priced = _priced(listings)
    if len(priced) < min_sample:
        return 0

    prices = [lst.price_usd for lst in priced]
    median = statistics.median(prices)
    if median <= 0:
        return 0
    mad = statistics.median([abs(p - median) for p in prices])

    low_flagged = 0
    for lst in priced:
        p = lst.price_usd
        below_ratio = p < median * low_ratio
        above_ratio = p > median * high_ratio
        # When MAD is 0 (prices identical-ish), fall back to the ratio test
        # alone so a lone cheap outlier among clones is still caught.
        mad_low = (median - p) > mad_k * mad if mad > 0 else below_ratio
        mad_high = (p - median) > mad_k * mad if mad > 0 else above_ratio

        if below_ratio and mad_low and FLAG_PRICE_ANOMALY_LOW not in lst.flags:
            lst.flags.append(FLAG_PRICE_ANOMALY_LOW)
            low_flagged += 1
        elif above_ratio and mad_high and FLAG_PRICE_ANOMALY_HIGH not in lst.flags:
            lst.flags.append(FLAG_PRICE_ANOMALY_HIGH)

    return low_flagged


def apply_ship_from_gate(
    listings: list[Listing],
    allowed_countries: list[str] | None,
    *,
    drop: bool = False,
) -> tuple[list[Listing], int]:
    """Flag / drop offers shipping from outside the allowed market.

    ``allowed_countries`` is a list of ISO 3166-1 alpha-2 codes (e.g.
    ``["US"]``). A listing whose ``ship_from_country`` is *known* and not in the
    set gets a ``ship_from_<cc>`` flag; with ``drop=True`` it is removed from the
    returned list. A listing with an unknown (``None``) origin is always kept and
    never flagged — we never guess a country.

    Serper listings carry no ship-from field, so this is effectively a no-op for
    them today; it becomes meaningful once the eBay adapter (Phase 33) populates
    real ``ship_from_country``. Returns ``(kept, flagged_count)``.

    When ``allowed_countries`` is falsy the gate is disabled (returns all).
    """
    if not allowed_countries:
        return listings, 0

    allowed = {c.strip().upper() for c in allowed_countries if c.strip()}
    kept: list[Listing] = []
    flagged = 0
    for lst in listings:
        cc = (lst.ship_from_country or "").strip().upper()
        if cc and cc not in allowed:
            flag = f"ship_from_{cc.lower()}"
            if flag not in lst.flags:
                lst.flags.append(flag)
            flagged += 1
            if drop:
                continue
        kept.append(lst)
    return kept, flagged
