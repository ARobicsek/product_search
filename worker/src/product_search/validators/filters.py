"""Filters for the validator pipeline.

Each `reject_*` function takes a Listing and a FilterRule.
It returns a string explaining the rejection if the listing fails the rule,
or None if the listing passes.

The `apply_filters` function runs all rules and returns the first rejection.
"""

from __future__ import annotations

from collections.abc import Callable

from product_search.models import Listing
from product_search.profile import FilterRule, Profile


def reject_form_factor_in(listing: Listing, rule: FilterRule) -> str | None:
    allowed: list[str] = (rule.model_extra or {}).get("values", [])
    ff = listing.attrs.get("form_factor")
    if ff is not None and ff not in allowed:
        return f"form_factor {ff!r} not in {allowed}"
    return None


def reject_speed_mts_min(listing: Listing, rule: FilterRule) -> str | None:
    min_speed: int = (rule.model_extra or {}).get("value", 0)
    speed = listing.attrs.get("speed_mts")
    if speed is not None and speed < min_speed:
        return f"speed {speed} MT/s is below minimum {min_speed}"
    return None


def reject_ecc_required(listing: Listing, rule: FilterRule) -> str | None:
    ecc = listing.attrs.get("ecc")
    # If ecc is explicitly False, reject. If True or None (unknown), allow.
    if ecc is False:
        return "non-ECC memory"
    return None


def reject_voltage_eq(listing: Listing, rule: FilterRule) -> str | None:
    req_v: float = (rule.model_extra or {}).get("value", 0.0)
    v = listing.attrs.get("voltage_v")
    if v is not None and v != req_v:
        return f"voltage {v}V != required {req_v}V"
    return None


def reject_min_quantity_for_target(
    listing: Listing, rule: FilterRule, profile: Profile
) -> str | None:
    """Reject if the listing definitely cannot fulfill the target.

    If quantity is unknown (None), we allow it to pass.
    If capacity is unknown (None), we allow it to pass.
    If capacity doesn't match any allowed configuration, reject.
    """
    cap = listing.attrs.get("capacity_gb")
    if cap is None:
        return None  # Unknown capacity; let downstream or manual review handle it.

    # Find matching configuration
    matched_config = None
    for config in profile.target.configurations:
        if config.module_capacity_gb == cap:
            matched_config = config
            break

    if not matched_config:
        return f"module capacity {cap}GB not in allowed target configurations"

    if listing.quantity_available is None:
        return None  # Unknown stock; assume it might have enough.

    total_modules_available = listing.quantity_available * listing.kit_module_count
    if total_modules_available < matched_config.module_count:
        return (
            f"insufficient quantity: need {matched_config.module_count} modules "
            f"for {profile.target.amount}GB target, but only {total_modules_available} available"
        )
    return None


def reject_in_stock(listing: Listing, rule: FilterRule) -> str | None:
    if listing.quantity_available is not None and listing.quantity_available <= 0:
        return "out of stock"
    return None


def reject_single_sku_url(listing: Listing, rule: FilterRule) -> str | None:
    # A basic heuristic: if the URL contains "search?" or "sch?", it's likely a search page.
    url = listing.url.lower()
    if "search?" in url or "sch?" in url or "/sch/" in url:
        return "URL appears to be a search results page, not a single SKU"
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_FILTER_FUNCS: dict[str, Callable[[Listing, FilterRule], str | None]] = {
    "form_factor_in": reject_form_factor_in,
    "speed_mts_min": reject_speed_mts_min,
    "ecc_required": reject_ecc_required,
    "voltage_eq": reject_voltage_eq,
    "in_stock": reject_in_stock,
    "single_sku_url": reject_single_sku_url,
}


def apply_filters(
    listing: Listing, rules: list[FilterRule], profile: Profile
) -> str | None:
    """Run all filter rules against the listing.

    Returns the first rejection reason string if rejected, else None.
    """
    for rule in rules:
        if rule.rule == "min_quantity_for_target":
            reason = reject_min_quantity_for_target(listing, rule, profile)
        else:
            func = _FILTER_FUNCS.get(rule.rule)
            if not func:
                # Should be prevented by Profile Pydantic validation, but fallback:
                continue
            reason = func(listing, rule)

        if reason is not None:
            return f"[{rule.rule}] {reason}"

    return None
