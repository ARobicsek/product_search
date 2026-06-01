"""Serper.dev shopping recall adapter (Phase 31, ADR-133/134).

Public API::

    from product_search.adapters.serper import fetch

    listings = fetch(query, fixture_path=None)

The recall primitive for the rebuild: instead of scraping vendors, we read
Google Shopping's *index* via Serper. Serper returns STRUCTURED fields
(``title``/``source``/``price``/``link``/``productId``/``imageUrl``/``rating``/
``ratingCount``), so the no-fabrication seam (ADR-001) holds — every price and
seller is a real field a deterministic fetch produced; the LLM never reads a
page or invents a number. Fields Serper doesn't carry (condition, stock count,
brand, mpn, ship-from) stay unknown — never guessed.

Fixture mode
------------
Set ``WORKER_USE_FIXTURES=1`` (or pass ``fixture_path`` explicitly) to read a
saved Serper JSON response instead of hitting the network. Unit tests pass a
single ``fixture_path``. Fixtures live in ``worker/tests/fixtures/serper/*.json``
and are the raw Serper response (top-level ``"shopping"`` list).

Live mode
---------
``POST https://google.serper.dev/shopping`` with header ``X-API-KEY`` from
``SERPER_API_KEY`` (env, or ``worker/.env`` for local runs). One request per
query string in ``query.queries``; results are de-duplicated by ``productId``
(falling back to ``link``). Missing key raises ``SerperAuthError``; a non-200
raises ``SerperAPIError`` — never a silent empty result.
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


class SerperAuthError(RuntimeError):
    """Raised when the Serper API key is absent."""


class SerperAPIError(RuntimeError):
    """Raised when the Serper API returns an error response."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"
_SOURCE_ID = "serper_shopping"  # the ADAPTER id (NOT the merchant)

_FIXTURE_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "serper"


def _fixture_dir() -> Path:
    if _FIXTURE_DIR.is_dir():
        return _FIXTURE_DIR
    cwd_path = Path.cwd() / "tests" / "fixtures" / "serper"
    if cwd_path.is_dir():
        return cwd_path
    for parent in Path.cwd().parents:
        candidate = parent / "worker" / "tests" / "fixtures" / "serper"
        if candidate.is_dir():
            return candidate
    return _FIXTURE_DIR  # return original so the error message is informative


# ---------------------------------------------------------------------------
# Key loading (mirrors scripts/serper_spike.py / cli.py: env or worker/.env)
# ---------------------------------------------------------------------------


def _load_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("SERPER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise SerperAuthError(
            "SERPER_API_KEY must be set (env or worker/.env) for live Serper fetches. "
            "Set WORKER_USE_FIXTURES=1 to use saved fixtures instead."
        )
    return key


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def parse_price(raw: Any) -> float | None:
    """Parse Serper's ``"$1,234.00"`` (or a bare number) into a float.

    Returns None when no number is present; the caller stores 0.0 as a
    sentinel and the Phase 32 price-sanity gate handles missing prices.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _PRICE_RE.search(str(raw).replace(",", ""))
    return float(m.group()) if m else None


def _result_to_listing(result: dict[str, Any]) -> Listing:
    """Map one Serper shopping result to a ``Listing``.

    ``attrs={}`` on purpose — Serper carries no structured spec fields; the
    ai_filter infers specs from the title, exactly as in production. ``condition``
    is ``""`` (honest unknown), NOT ``"unknown"``: ``reject_condition_in`` passes
    on empty (``if cond and cond not in allowed``) but would reject a literal
    ``"unknown"`` against ``condition_in:[new]`` — i.e. reject 100% of Serper
    listings. The spike's runtest used ``"unknown"``; that was a bug for the
    real pipeline. (REBUILD_PLAN §0 "honored where known, else degrade honestly".)
    """
    price = parse_price(result.get("price"))
    link = result.get("link") or ""

    attrs: dict[str, Any] = {}
    product_id = result.get("productId")
    if product_id is not None:
        # Kept for future dedup / merchant-link resolution (REBUILD_PLAN §0 seam).
        attrs["serper_product_id"] = product_id

    rating_raw = result.get("rating")
    rating_count_raw = result.get("ratingCount")

    image_url = result.get("imageUrl")
    if isinstance(image_url, str) and image_url.startswith("data:"):
        image_url = None

    return Listing(
        source=_SOURCE_ID,
        # Serper's ``link`` is ALWAYS a google.com/search shopping-cluster
        # redirect — the only link Serper gives. We do NOT fabricate a merchant
        # URL (honesty). ``url`` and ``buy_url`` are the same today; a future
        # resolver can rewrite ``buy_url`` to a direct merchant link.
        url=link,
        title=result.get("title") or "",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs=attrs,
        condition="",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=price if price is not None else 0.0,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=result.get("source") or "",
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
        buy_url=link,
        image_url=image_url,
        rating=float(rating_raw) if isinstance(rating_raw, (int, float)) else None,
        rating_count=int(rating_count_raw) if isinstance(rating_count_raw, (int, float)) else None,
    )


def _results_to_listings(results: list[dict[str, Any]]) -> list[Listing]:
    """Map + de-duplicate Serper results by productId (fallback: link)."""
    seen: set[str] = set()
    listings: list[Listing] = []
    for r in results:
        key = str(r.get("productId") or r.get("link") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        listings.append(_result_to_listing(r))
    return listings


# ---------------------------------------------------------------------------
# Fixture fetch
# ---------------------------------------------------------------------------


def _read_shopping(data: dict[str, Any]) -> list[dict[str, Any]]:
    shopping = data.get("shopping", [])
    return shopping if isinstance(shopping, list) else []


def _fetch_fixture(query: AdapterQuery) -> list[Listing]:
    """Return Listings from every saved Serper fixture in the fixture dir."""
    fixture_dir = _fixture_dir()
    files = sorted(fixture_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"No fixture files found in {fixture_dir}. "
            "At least one .json fixture is required for WORKER_USE_FIXTURES=1 mode."
        )
    results: list[dict[str, Any]] = []
    for fpath in files:
        data: dict[str, Any] = json.loads(fpath.read_text(encoding="utf-8"))
        results.extend(_read_shopping(data))
    return _results_to_listings(results)


# ---------------------------------------------------------------------------
# Live fetch (requires SERPER_API_KEY)
# ---------------------------------------------------------------------------


def _fetch_live(query: AdapterQuery) -> list[Listing]:
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "httpx is required for live Serper fetches. Run: pip install 'httpx>=0.27'"
        ) from exc

    key = _load_key()
    gl = str(query.extra.get("gl", "us"))
    num = int(query.extra.get("num", 40))
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}

    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=45.0) as client:
        for q in query.queries:
            resp = client.post(
                SERPER_SHOPPING_URL,
                headers=headers,
                json={"q": q, "gl": gl, "num": num},
            )
            if resp.status_code != 200:
                raise SerperAPIError(
                    f"Serper API error ({resp.status_code}) for query {q!r}: "
                    f"{resp.text[:400]}"
                )
            results.extend(_read_shopping(resp.json()))

    return _results_to_listings(results)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch(
    query: AdapterQuery,
    *,
    fixture_path: Path | None = None,
) -> list[Listing]:
    """Fetch Serper shopping listings for the given query.

    Args:
        query: Search parameters; ``queries`` drives the shopping requests,
            ``extra`` may carry ``gl`` / ``num``.
        fixture_path: If given, load this specific Serper JSON file instead of
            hitting the network (single-file fixture mode used in tests).

    Returns:
        A de-duplicated list of ``Listing`` objects (may be empty).

    Raises:
        ``SerperAuthError``: live mode with no key.
        ``SerperAPIError``: the Serper API returned a non-200.
        ``FileNotFoundError``: fixture mode with no fixtures present.
    """
    use_fixtures = (
        os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
        or fixture_path is not None
    )

    if use_fixtures:
        if fixture_path is not None:
            data: dict[str, Any] = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
            return _results_to_listings(_read_shopping(data))
        return _fetch_fixture(query)

    return _fetch_live(query)
