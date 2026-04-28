"""CloudStorageCorp eBay store scraper.

Public API::

    from product_search.adapters.cloudstoragecorp import fetch

    listings = fetch(query, fixture_path=None)
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from product_search.models import AdapterQuery, Listing

_FIXTURE_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "cloudstoragecorp"

def _fixture_dir() -> Path:
    if _FIXTURE_DIR.is_dir():
        return _FIXTURE_DIR
    cwd_path = Path.cwd() / "tests" / "fixtures" / "cloudstoragecorp"
    if cwd_path.is_dir():
        return cwd_path
    for parent in Path.cwd().parents:
        candidate = parent / "worker" / "tests" / "fixtures" / "cloudstoragecorp"
        if candidate.is_dir():
            return candidate
    return _FIXTURE_DIR


def _parse_capacity(title: str) -> int | None:
    m = re.search(r"\b(\d+)\s*GB\b", title, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_speed(title: str) -> int | None:
    m = re.search(r"\b(4800|5200|5600|6000|6400)(?:MHz|\s*MT)?\b", title, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_condition(cond: str) -> str:
    cond = cond.lower()
    if "new" in cond and "pre" not in cond:
        return "new"
    if "refurb" in cond:
        return "refurbished"
    return "used"


def _parse_html(html: str) -> list[Listing]:
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        raise ImportError("selectolax is required. Run: pip install selectolax")
        
    tree = HTMLParser(html)
    listings: list[Listing] = []
    fetched_at = datetime.now(tz=UTC)

    for item in tree.css(".s-item__wrapper"):
        title_node = item.css_first(".s-item__title span")
        if not title_node:
            continue
        title = title_node.text().strip()

        link_node = item.css_first(".s-item__link")
        url = link_node.attributes.get("href", "") if link_node else ""

        price_node = item.css_first(".s-item__price")
        price_str = price_node.text().replace("$", "").replace(",", "").strip() if price_node else "0"
        try:
            price_usd = float(price_str)
        except ValueError:
            continue
            
        cond_node = item.css_first(".SECONDARY_INFO")
        condition_raw = cond_node.text().strip() if cond_node else "Used"

        capacity_gb = _parse_capacity(title)
        speed_mts = _parse_speed(title)
        attrs = {}
        if capacity_gb:
            attrs["capacity_gb"] = capacity_gb
        if speed_mts:
            attrs["speed_mts"] = speed_mts

        listings.append(Listing(
            source="cloudstoragecorp_ebay",
            url=url,
            title=title,
            fetched_at=fetched_at,
            brand=None,
            mpn=None,
            attrs=attrs,
            condition=_parse_condition(condition_raw),
            is_kit=False,
            kit_module_count=1,
            unit_price_usd=price_usd,
            kit_price_usd=None,
            quantity_available=None,
            seller_name="cloudstoragecorp",
            seller_rating_pct=None,
            seller_feedback_count=None,
            ship_from_country="US",
        ))

    return listings


def _fetch_fixture(query: AdapterQuery) -> list[Listing]:
    fixture_dir = _fixture_dir()
    listings: list[Listing] = []
    files = sorted(fixture_dir.glob("*.html"))
    if not files:
        raise FileNotFoundError(f"No fixture files found in {fixture_dir}.")
    
    for fpath in files:
        html = fpath.read_text(encoding="utf-8")
        listings.extend(_parse_html(html))
    return listings


def _fetch_live(query: AdapterQuery) -> list[Listing]:
    import httpx
    
    # Target e.g. https://www.ebay.com/sch/cloudstoragecorp/m.html
    url = f"https://www.ebay.com/sch/{query.seller_id or 'cloudstoragecorp'}/m.html"
    
    with httpx.Client(timeout=20.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            return []
        return _parse_html(resp.text)


def fetch(
    query: AdapterQuery,
    *,
    fixture_path: Path | None = None,
) -> list[Listing]:
    """Fetch CloudStorageCorp listings."""
    use_fixtures = (
        os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
        or fixture_path is not None
    )

    if use_fixtures:
        if fixture_path is not None:
            html = fixture_path.read_text(encoding="utf-8")
            return _parse_html(html)
        return _fetch_fixture(query)

    return _fetch_live(query)
