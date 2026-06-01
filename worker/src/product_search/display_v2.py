"""Type-aware display column resolution (Phase 32, REBUILD_PLAN §7).

A display renders an attribute column only if it is (a) relevant to the
``product_type`` **and** (b) populated for at least one shown listing. A
subscription shows term/price/vendor; a drone shows price/condition/seller;
never "color" for a magazine.

The profile's ``display.attrs`` (set by the onboarder, v2) is the curated,
type-relevant column list; this module starts from it — falling back to a
sensible per-type default when it's empty — then drops any column not populated
in the displayed listings, so the UI never shows an all-blank column.

Pure + deterministic.
"""

from __future__ import annotations

from collections.abc import Callable

from product_search.models import Listing

# Canonical column keys → "is this populated for this listing?" predicate.
# ``price`` is always considered populated (it's the point of the tool, and an
# unpriced listing still occupies a row). Everything else must have real data.
_POPULATED: dict[str, Callable[[Listing], bool]] = {
    "price": lambda lst: True,
    "condition": lambda lst: bool((lst.condition or "").strip()),
    "seller": lambda lst: bool((lst.seller_name or "").strip()),
    "seller_rating": lambda lst: lst.seller_rating_pct is not None,
    "rating": lambda lst: lst.rating is not None,
    "rating_count": lambda lst: lst.rating_count is not None,
    "quantity": lambda lst: lst.quantity_available is not None,
    "ship_from": lambda lst: bool((lst.ship_from_country or "").strip()),
    "brand": lambda lst: bool((lst.brand or "").strip()),
    "mpn": lambda lst: bool((lst.mpn or "").strip()),
    "term": lambda lst: bool(str((lst.attrs or {}).get("term", "")).strip()),
}

# Per-type default column order, used when ``display.attrs`` is empty. The
# generic default works for any product. Free-form ``product_type`` values that
# aren't listed fall back to the generic default.
_GENERIC_DEFAULT = ["price", "condition", "seller", "seller_rating"]
_TYPE_DEFAULTS: dict[str, list[str]] = {
    "drone": ["price", "condition", "seller", "seller_rating", "rating"],
    "electronics": ["price", "condition", "seller", "seller_rating", "rating"],
    "subscription": ["price", "term", "seller"],
    "book": ["price", "condition", "seller", "rating"],
    "grocery": ["price", "seller", "quantity"],
}


def default_columns_for_type(product_type: str | None) -> list[str]:
    """The fallback column order for a product type (generic if unknown)."""
    if not product_type:
        return list(_GENERIC_DEFAULT)
    return list(_TYPE_DEFAULTS.get(product_type.strip().lower(), _GENERIC_DEFAULT))


def resolve_columns(
    *,
    profile_attrs: list[str],
    product_type: str | None,
    displayed: list[Listing],
) -> list[str]:
    """Resolve the ordered display columns for a run.

    Starts from ``profile_attrs`` (the onboarder's curated, type-relevant list),
    or the per-type default when that's empty, then keeps only columns populated
    for ≥1 displayed listing. Unknown column keys are dropped. ``price`` is
    always retained (it leads every product type).
    """
    base = [c for c in profile_attrs if c] or default_columns_for_type(product_type)

    out: list[str] = []
    for col in base:
        key = col.strip().lower()
        pred = _POPULATED.get(key)
        if pred is None:
            # Dynamic attr from extracted_features — show if any
            # displayed listing carries a non-empty value in its attrs dict.
            if any(
                bool(str((lst.attrs or {}).get(key, "")).strip())
                for lst in displayed
            ):
                if key not in out:
                    out.append(key)
            continue
        if key == "price" or any(pred(lst) for lst in displayed):
            if key not in out:
                out.append(key)
    if "price" not in out:
        out.insert(0, "price")
    return out
