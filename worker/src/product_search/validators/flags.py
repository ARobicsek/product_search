"""Flags for the validator pipeline.

Each `flag_*` function takes a Listing and a FlagRule.
It returns True if the flag rule applies (meaning `rule.flag` should be
appended to the listing's flags), or False otherwise.

The `apply_flags` function runs all rules and updates the listing.
"""

from __future__ import annotations

from product_search.models import Listing
from product_search.profile import FlagRule


def flag_ship_from_country_in(listing: Listing, rule: FlagRule) -> bool:
    countries: list[str] = (rule.model_extra or {}).get("values", [])
    if not listing.ship_from_country:
        return False
    return listing.ship_from_country.upper() in [c.upper() for c in countries]


def flag_brand_in(listing: Listing, rule: FlagRule) -> bool:
    brands: list[str] = (rule.model_extra or {}).get("values", [])
    if not listing.brand:
        return False
    b = listing.brand.lower()
    return any(target.lower() in b for target in brands)


def flag_kingston_e_suffix(listing: Listing, rule: FlagRule) -> bool:
    if not listing.brand or not listing.mpn:
        return False
    if "kingston" not in listing.brand.lower():
        return False
    # Kingston part numbers ending in E are UDIMMs, not RDIMMs.
    return listing.mpn.upper().endswith("E")


def flag_title_mentions_other_server(listing: Listing, rule: FlagRule) -> bool:
    mentions: list[str] = (rule.model_extra or {}).get("values", [])
    title = listing.title.lower()
    return any(m.lower() in title for m in mentions)


def flag_title_mentions(listing: Listing, rule: FlagRule) -> bool:
    mentions: list[str] = (rule.model_extra or {}).get("values", [])
    title = listing.title.lower()
    return any(m.lower() in title for m in mentions)


def flag_low_seller_feedback(listing: Listing, rule: FlagRule) -> bool:
    rating_below: float = (rule.model_extra or {}).get("rating_pct_below", 0.0)
    count_below: int = (rule.model_extra or {}).get("count_below", 0)

    # If feedback is unknown, we flag it just in case.
    if listing.seller_rating_pct is None or listing.seller_feedback_count is None:
        return True

    if listing.seller_rating_pct < rating_below:
        return True
    if listing.seller_feedback_count < count_below:
        return True

    return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_FLAG_FUNCS = {
    "ship_from_country_in": flag_ship_from_country_in,
    "brand_in": flag_brand_in,
    "kingston_e_suffix": flag_kingston_e_suffix,
    "title_mentions_other_server": flag_title_mentions_other_server,
    "title_mentions": flag_title_mentions,
    "low_seller_feedback": flag_low_seller_feedback,
}


def apply_flags(listing: Listing, rules: list[FlagRule]) -> None:
    """Run all flag rules against the listing and mutate `listing.flags`."""
    for rule in rules:
        func = _FLAG_FUNCS.get(rule.rule)
        if not func:
            continue

        if func(listing, rule):
            if rule.flag not in listing.flags:
                listing.flags.append(rule.flag)
