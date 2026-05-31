"""Type-aware display column resolution (Phase 32, REBUILD_PLAN §7)."""

from __future__ import annotations

from datetime import UTC, datetime

from product_search.display_v2 import default_columns_for_type, resolve_columns
from product_search.models import Listing


def _listing(
    *,
    condition: str = "",
    seller: str = "B&H",
    seller_rating: float | None = None,
    rating: float | None = None,
    attrs: dict | None = None,
) -> Listing:
    return Listing(
        source="serper_shopping",
        url="x",
        title="t",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs=dict(attrs or {}),
        condition=condition,
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=10.0,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=seller,
        seller_rating_pct=seller_rating,
        seller_feedback_count=None,
        ship_from_country=None,
        rating=rating,
    )


def test_drops_unpopulated_columns() -> None:
    # condition blank + seller_rating None across all → both dropped; seller kept.
    displayed = [_listing(condition="", seller="B&H", seller_rating=None)]
    cols = resolve_columns(
        profile_attrs=["price", "condition", "seller", "seller_rating"],
        product_type="drone",
        displayed=displayed,
    )
    assert cols == ["price", "seller"]


def test_keeps_populated_column() -> None:
    displayed = [_listing(condition="new", seller="B&H", seller_rating=99.1)]
    cols = resolve_columns(
        profile_attrs=["price", "condition", "seller", "seller_rating"],
        product_type="drone",
        displayed=displayed,
    )
    assert cols == ["price", "condition", "seller", "seller_rating"]


def test_falls_back_to_type_default_when_attrs_empty() -> None:
    displayed = [_listing(attrs={"term": "1 year"})]
    cols = resolve_columns(
        profile_attrs=[],
        product_type="subscription",
        displayed=displayed,
    )
    assert cols[0] == "price"
    assert "term" in cols  # subscription default includes term, and it's populated


def test_unknown_column_dropped() -> None:
    cols = resolve_columns(
        profile_attrs=["price", "color"],  # "color" isn't a known key
        product_type=None,
        displayed=[_listing()],
    )
    assert "color" not in cols
    assert cols == ["price"]


def test_price_always_present() -> None:
    cols = resolve_columns(profile_attrs=[], product_type=None, displayed=[])
    assert "price" in cols


def test_default_columns_for_unknown_type_is_generic() -> None:
    assert default_columns_for_type("zzz-unknown") == default_columns_for_type(None)
