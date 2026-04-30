"""NEMIX RAM Shopify API adapter.

Public API::

    from product_search.adapters.nemixram import fetch

    listings = fetch(query, fixture_path=None)

This adapter uses Shopify's public `/products.json` endpoints.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.models import AdapterQuery, Listing

_FIXTURE_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "nemixram"

def _fixture_dir() -> Path:
    if _FIXTURE_DIR.is_dir():
        return _FIXTURE_DIR
    cwd_path = Path.cwd() / "tests" / "fixtures" / "nemixram"
    if cwd_path.is_dir():
        return cwd_path
    for parent in Path.cwd().parents:
        candidate = parent / "worker" / "tests" / "fixtures" / "nemixram"
        if candidate.is_dir():
            return candidate
    return _FIXTURE_DIR


def _parse_capacity(title: str) -> int | None:
    m = re.search(r"\b(\d+)\s*GB\b", title, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_speed(title: str) -> int | None:
    m = re.search(r"\b(4800|5200|5600|6000|6400)\b", title)
    return int(m.group(1)) if m else None


def _product_to_listings(product: dict[str, Any], base_url: str) -> list[Listing]:
    """Convert one Shopify product with variants into Listings."""
    listings: list[Listing] = []
    
    title = product.get("title", "")
    handle = product.get("handle", "")
    url = f"{base_url}/products/{handle}" if base_url else handle
    vendor = product.get("vendor", "NEMIX RAM")
    
    capacity_gb = _parse_capacity(title)
    speed_mts = _parse_speed(title)
    
    attrs: dict[str, Any] = {}
    if capacity_gb is not None:
        attrs["capacity_gb"] = capacity_gb
    if speed_mts is not None:
        attrs["speed_mts"] = speed_mts

    fetched_at = datetime.now(tz=UTC)

    for variant in product.get("variants", []):
        if not variant.get("available", False):
            continue
            
        price_str = variant.get("price", "0")
        try:
            price_usd = float(price_str)
        except ValueError:
            continue
            
        quantity = variant.get("inventory_quantity")
        if quantity is not None:
            quantity = int(quantity)

        sku = variant.get("sku")

        listings.append(Listing(
            source="nemixram_storefront",
            url=url,
            title=f"{title} (Variant: {sku})" if sku else title,
            fetched_at=fetched_at,
            brand=vendor,
            mpn=sku,
            attrs=attrs,
            condition="new",  # NEMIX RAM sells new RAM
            is_kit=False,     # Simplified for now
            kit_module_count=1,
            unit_price_usd=price_usd,
            kit_price_usd=None,
            quantity_available=quantity,
            seller_name="NEMIX RAM",
            seller_rating_pct=100.0,
            seller_feedback_count=None,
            ship_from_country="US",
        ))
        
    return listings


def _fetch_fixture(query: AdapterQuery) -> list[Listing]:
    fixture_dir = _fixture_dir()
    listings: list[Listing] = []
    files = sorted(fixture_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No fixture files found in {fixture_dir}.")
    
    for fpath in files:
        data: dict[str, Any] = json.loads(fpath.read_text(encoding="utf-8"))
        for product in data.get("products", []):
            listings.extend(_product_to_listings(product, "https://nemixram.com"))
    return listings


def _fetch_live(query: AdapterQuery) -> list[Listing]:
    storefront_url = query.storefront_url
    if not storefront_url:
        return []
    
    import httpx
    
    # Target e.g., https://nemixram.com/collections/ddr5-rdimm/products.json
    api_url = storefront_url.rstrip("/") + "/products.json"
    
    listings: list[Listing] = []
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(api_url, params={"limit": query.max_results_per_query})
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:100]}")
            
        data = resp.json()
        base_url = "/".join(storefront_url.split("/")[:3]) # e.g. https://nemixram.com
        for product in data.get("products", []):
            listings.extend(_product_to_listings(product, base_url))

    return listings


def fetch(
    query: AdapterQuery,
    *,
    fixture_path: Path | None = None,
) -> list[Listing]:
    """Fetch Nemix RAM listings."""
    use_fixtures = (
        os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
        or fixture_path is not None
    )

    if use_fixtures:
        if fixture_path is not None:
            data: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
            listings = []
            for product in data.get("products", []):
                listings.extend(_product_to_listings(product, "https://nemixram.com"))
            return listings
        return _fetch_fixture(query)

    return _fetch_live(query)
