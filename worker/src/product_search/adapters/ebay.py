"""eBay Browse API adapter.

Public API::

    from product_search.adapters.ebay import fetch

    listings = fetch(query, fixture_path=None)

Fixture mode
------------
Set ``WORKER_USE_FIXTURES=1`` (or pass ``fixture_path`` explicitly) to read
from a saved JSON file instead of hitting the network.  This lets all other
components be developed and tested without live eBay credentials.

Live mode
---------
Requires ``EBAY_CLIENT_ID`` and ``EBAY_CLIENT_SECRET`` in the environment.
These arrive when the developer app is approved (pending as of 2026-04-28).
Calling ``fetch()`` in live mode without credentials raises ``EbayAuthError``
immediately rather than silently returning empty results.

Response shape parsed
---------------------
eBay Browse API v1 ``GET /buy/browse/v1/item_summary/search``.
Key fields extracted per listing:

    itemId, title, itemWebUrl, condition, price.value, seller.*,
    itemLocation.country, estimatedAvailabilities[0].*,
    mpn (item aspect), brand (item aspect)

Fields not available from the search summary endpoint (e.g. exact rank
``2Rx4``) are left as ``None``; Phase 3 validators handle ``None`` correctly.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.models import AdapterQuery, Listing

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EbayAuthError(RuntimeError):
    """Raised when eBay credentials are absent or invalid."""


class EbayAPIError(RuntimeError):
    """Raised when the eBay API returns an error response."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BROWSE_API_BASE = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_AUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_FIXTURE_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "ebay"
# Fallback: relative to CWD (when adapter is invoked from worker/ in CI)
_FIXTURE_DIR_CWD = Path("tests") / "fixtures" / "ebay"


def _fixture_dir() -> Path:
    if _FIXTURE_DIR.is_dir():
        return _FIXTURE_DIR
    cwd_path = Path.cwd() / "tests" / "fixtures" / "ebay"
    if cwd_path.is_dir():
        return cwd_path
    # Last resort: walk up from CWD looking for worker/tests/fixtures/ebay
    for parent in Path.cwd().parents:
        candidate = parent / "worker" / "tests" / "fixtures" / "ebay"
        if candidate.is_dir():
            return candidate
    return _FIXTURE_DIR  # return original so error message is informative


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Keywords that suggest a listing is a multi-module kit.
_KIT_PATTERNS = re.compile(
    r"\b(\d+)\s*x\s*(\d+)\s*gb\b"        # "8x32GB"
    r"|\b(\d+)(?:\s*[-–]|\s+)pack\b"     # "8-pack" / "8 pack"
    r"|\bkit\s+of\s+(\d+)\b"             # "kit of 8"
    r"|\b(\d+)\s*pcs?\b",                # "8pcs"
    re.IGNORECASE,
)


def _parse_condition(condition_str: str) -> str:
    """Normalise eBay condition string to 'new' | 'used' | 'refurbished'."""
    c = condition_str.lower()
    if "new" in c:
        return "new"
    if "refurb" in c or "certified" in c or "renew" in c:
        return "refurbished"
    return "used"


def _parse_kit(title: str, price_usd: float) -> tuple[bool, int, float, float | None]:
    """Return (is_kit, kit_module_count, unit_price_usd, kit_price_usd)."""
    m = _KIT_PATTERNS.search(title)
    if m:
        # Extract the module count from whichever group matched.
        count_str = next(g for g in m.groups() if g is not None)
        try:
            count = int(count_str)
        except ValueError:
            count = 1
        if count > 1:
            return True, count, round(price_usd / count, 2), price_usd
    return False, 1, price_usd, None


def _parse_capacity_from_title(title: str) -> int | None:
    """Try to extract module capacity in GB from the listing title."""
    # Match patterns like "32GB", "64 GB"
    m = re.search(r"\b(\d+)\s*GB\b", title, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        # Sanity check — module capacity for RDIMM is typically 8-256 GB
        if 8 <= val <= 256:
            return val
    return None


def _parse_speed_from_title(title: str) -> int | None:
    """Try to extract speed in MT/s from the title."""
    # PC5-38400R -> 38400 / 8 = 4800 MT/s
    m = re.search(r"PC5-(\d{4,5})R?\b", title, re.IGNORECASE)
    if m:
        return int(m.group(1)) // 8

    # "DDR5-4800" or "DDR5 4800"
    m = re.search(r"DDR5[-\s](\d{4,5})\b", title, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # Bare speed followed by optional "MHz" or "MT/s" or standalone
    m = re.search(r"\b(4800|5200|5600|6000|6400)(?:MHz|\s*MT)?\b", title, re.IGNORECASE)
    if m:
        return int(m.group(1))

    return None


def _item_to_listing(item: dict[str, Any]) -> Listing:
    """Convert one eBay Browse API itemSummary dict to a ``Listing``."""
    title: str = item.get("title", "")
    url: str = item.get("itemWebUrl", "")
    condition_raw: str = item.get("condition", "Used")
    price_str: str = item.get("price", {}).get("value", "0")
    price_usd = float(price_str)

    is_kit, kit_module_count, unit_price_usd, kit_price_usd = _parse_kit(title, price_usd)

    seller: dict[str, Any] = item.get("seller", {})
    seller_name: str = seller.get("username", "")
    rating_str = seller.get("feedbackPercentage")
    seller_rating_pct: float | None = float(rating_str) if rating_str else None
    feedback_count_raw = seller.get("feedbackScore")
    seller_feedback_count: int | None = int(feedback_count_raw) if feedback_count_raw else None

    location: dict[str, Any] = item.get("itemLocation", {})
    ship_from_country: str | None = location.get("country") or None

    avail_list: list[dict[str, Any]] = item.get("estimatedAvailabilities", [])
    quantity_available: int | None = None
    if avail_list:
        avail = avail_list[0]
        if avail.get("estimatedAvailabilityStatus") == "IN_STOCK":
            qty = avail.get("estimatedAvailableQuantity")
            quantity_available = int(qty) if qty is not None else None

    # MPN / brand can come from top-level fields or aspects.
    mpn: str | None = item.get("mpn") or None
    brand: str | None = item.get("brand") or None

    capacity_gb = _parse_capacity_from_title(title)
    speed_mts = _parse_speed_from_title(title)

    # Build attrs dict from what we can parse; leave missing fields absent.
    attrs: dict[str, Any] = {}
    if capacity_gb is not None:
        attrs["capacity_gb"] = capacity_gb
    if speed_mts is not None:
        attrs["speed_mts"] = speed_mts
    # form_factor and ecc are hard to extract from titles reliably;
    # the validator will check these come Phase 3.

    return Listing(
        source="ebay_search",
        url=url,
        fetched_at=datetime.now(tz=UTC),
        brand=brand,
        mpn=mpn,
        attrs=attrs,
        condition=_parse_condition(condition_raw),
        is_kit=is_kit,
        kit_module_count=kit_module_count,
        unit_price_usd=unit_price_usd,
        kit_price_usd=kit_price_usd,
        quantity_available=quantity_available,
        seller_name=seller_name,
        seller_rating_pct=seller_rating_pct,
        seller_feedback_count=seller_feedback_count,
        ship_from_country=ship_from_country,
    )


# ---------------------------------------------------------------------------
# Fixture fetch
# ---------------------------------------------------------------------------


def _fetch_fixture(query: AdapterQuery) -> list[Listing]:
    """Return Listings from saved fixture JSON files.

    Loads every ``.json`` file in the fixture directory and parses it as an
    eBay Browse API search response.  Query parameters are ignored — fixtures
    represent a representative snapshot.
    """
    fixture_dir = _fixture_dir()
    listings: list[Listing] = []
    files = sorted(fixture_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"No fixture files found in {fixture_dir}. "
            "At least one .json fixture is required for WORKER_USE_FIXTURES=1 mode."
        )
    for fpath in files:
        data: dict[str, Any] = json.loads(fpath.read_text(encoding="utf-8"))
        for item in data.get("itemSummaries", []):
            listings.append(_item_to_listing(item))
    return listings


# ---------------------------------------------------------------------------
# Live fetch (requires eBay credentials)
# ---------------------------------------------------------------------------


def _get_access_token(client_id: str, client_secret: str) -> str:
    """Obtain an OAuth2 client-credentials access token from eBay."""
    try:
        import base64

        import httpx
    except ImportError as exc:
        raise ImportError(
            "httpx is required for live eBay fetches. Run: pip install 'httpx>=0.27'"
        ) from exc

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = httpx.post(
        _AUTH_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise EbayAuthError(
            f"eBay OAuth failed ({resp.status_code}): {resp.text[:400]}"
        )
    token: str = resp.json()["access_token"]
    return token


def _fetch_live(query: AdapterQuery) -> list[Listing]:
    """Fetch listings from the live eBay Browse API.

    Requires ``EBAY_CLIENT_ID`` and ``EBAY_CLIENT_SECRET`` in environment.
    Each query string in ``query.queries`` is searched separately; results
    are de-duplicated by ``itemId``.
    """
    client_id = os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise EbayAuthError(
            "EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be set for live eBay fetches. "
            "Set WORKER_USE_FIXTURES=1 to use saved fixtures instead."
        )

    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "httpx is required for live eBay fetches. Run: pip install 'httpx>=0.27'"
        ) from exc

    token = _get_access_token(client_id, client_secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "Content-Type": "application/json",
    }

    seen_ids: set[str] = set()
    listings: list[Listing] = []

    with httpx.Client(timeout=20.0) as client:
        for search_term in query.queries:
            params: dict[str, str | int] = {
                "q": search_term,
                "limit": min(query.max_results_per_query, 200),
                "filter": "buyingOptions:{FIXED_PRICE}",
            }
            resp = client.get(_BROWSE_API_BASE, headers=headers, params=params)
            if resp.status_code != 200:
                raise EbayAPIError(
                    f"eBay Browse API error ({resp.status_code}) for query {search_term!r}: "
                    f"{resp.text[:400]}"
                )
            data: dict[str, Any] = resp.json()
            for item in data.get("itemSummaries", []):
                item_id: str = item.get("itemId", "")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                listings.append(_item_to_listing(item))

    return listings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch(
    query: AdapterQuery,
    *,
    fixture_path: Path | None = None,
) -> list[Listing]:
    """Fetch eBay listings for the given query.

    Args:
        query: Search parameters from the product profile.
        fixture_path: If given, load from this specific fixture file instead
            of auto-discovering fixture dir.  Only used in tests.

    Returns:
        List of ``Listing`` objects (may be empty if the search returns nothing).

    Raises:
        ``EbayAuthError``: if live mode and credentials are missing/invalid.
        ``EbayAPIError``: if the eBay API returns an error.
        ``FileNotFoundError``: if fixture mode and no fixtures are present.
    """
    use_fixtures = (
        os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
        or fixture_path is not None
    )

    if use_fixtures:
        if fixture_path is not None:
            # Single-file fixture (used in unit tests)
            data: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
            return [_item_to_listing(item) for item in data.get("itemSummaries", [])]
        return _fetch_fixture(query)

    return _fetch_live(query)
