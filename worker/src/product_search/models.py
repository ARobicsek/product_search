"""Core data models for the product-search worker.

``Listing`` is the single shared shape every source adapter produces.
Downstream code (validators, storage, synthesizer) only ever sees ``Listing``.

``AdapterQuery`` carries the search parameters from a profile's ``sources``
entry to the adapter; it is intentionally product-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AdapterQuery:
    """Parameters passed from a profile's ``sources`` entry to an adapter.

    Each adapter reads the keys it needs; unknown keys are ignored so the
    same query dict works for all adapters.
    """

    source_id: str
    # eBay search adapter
    queries: list[str] = field(default_factory=list)
    max_results_per_query: int = 50
    # Storefront / seller-page adapters
    storefront_url: str | None = None
    seller_id: str | None = None
    # Pass-through of any extra keys from the profile YAML
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_profile_source(cls, source: dict[str, Any]) -> AdapterQuery:
        """Build an ``AdapterQuery`` from a raw profile ``sources`` dict entry."""
        known_keys = {"id", "queries", "max_results_per_query", "storefront_url", "seller_id"}
        extra = {k: v for k, v in source.items() if k not in known_keys}
        return cls(
            source_id=source["id"],
            queries=source.get("queries", []),
            max_results_per_query=source.get("max_results_per_query", 50),
            storefront_url=source.get("storefront_url"),
            seller_id=source.get("seller_id"),
            extra=extra,
        )


@dataclass
class Listing:
    """A single product listing, as it comes from any source adapter.

    Fields the adapter can't populate must be left as ``None``; they must
    never be guessed.  The validator pipeline interprets ``None`` as
    "unknown" — and may reject or flag based on that.
    """

    # --- Provenance -----------------------------------------------------------
    source: str              # "ebay_search", "nemixram_storefront", …
    url: str                 # Direct product URL — never a search-results page
    fetched_at: datetime

    # --- Product --------------------------------------------------------------
    brand: str | None
    mpn: str | None          # Manufacturer part number
    # Product-type-specific spec fields: capacity_gb, speed_mts, etc.
    # Keys are declared in profile.spec_attrs; validator enforces required ones.
    attrs: dict[str, Any]

    # --- Listing details ------------------------------------------------------
    condition: str           # "new" | "used" | "refurbished"
    is_kit: bool             # True when the listing is a multi-module kit
    kit_module_count: int    # 1 for single; N for a kit of N
    unit_price_usd: float    # Price of one module
    kit_price_usd: float | None  # Total kit price (None when is_kit=False)
    quantity_available: int | None   # None = unknown, not 0

    # --- Seller ---------------------------------------------------------------
    seller_name: str
    seller_rating_pct: float | None   # e.g. 99.5 (percent)
    seller_feedback_count: int | None
    ship_from_country: str | None     # ISO 3166-1 alpha-2, e.g. "US", "CN"

    # --- Set by the validator pipeline ----------------------------------------
    qvl_status: str | None = None
    # "qvl" | "inferred-compatible" | "unknown" | "incompatible"
    flags: list[str] = field(default_factory=list)
    total_for_target_usd: float | None = None  # cheapest way to hit the target

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (datetime → ISO string)."""
        d: dict[str, Any] = {
            "source": self.source,
            "url": self.url,
            "fetched_at": self.fetched_at.isoformat(),
            "brand": self.brand,
            "mpn": self.mpn,
            "attrs": self.attrs,
            "condition": self.condition,
            "is_kit": self.is_kit,
            "kit_module_count": self.kit_module_count,
            "unit_price_usd": self.unit_price_usd,
            "kit_price_usd": self.kit_price_usd,
            "quantity_available": self.quantity_available,
            "seller_name": self.seller_name,
            "seller_rating_pct": self.seller_rating_pct,
            "seller_feedback_count": self.seller_feedback_count,
            "ship_from_country": self.ship_from_country,
            "qvl_status": self.qvl_status,
            "flags": self.flags,
            "total_for_target_usd": self.total_for_target_usd,
        }
        return d

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)
