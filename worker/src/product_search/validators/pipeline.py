"""Pipeline orchestration for the validator phase.

Runs filters, flags, QVL annotation, and calculates `total_for_target_usd`.
"""

from __future__ import annotations

from product_search.models import Listing
from product_search.profile import QVL, Profile
from product_search.validators.filters import apply_filters
from product_search.validators.flags import apply_flags
from product_search.validators.qvl import annotate_qvl


def _calculate_total(listing: Listing, profile: Profile) -> float | None:
    """Calculate `total_for_target_usd` if the listing meets the target."""
    cap = listing.attrs.get("capacity_gb")
    if cap is None:
        return None

    # Find matching configuration
    matched_config = None
    for config in profile.target.configurations:
        if config.module_capacity_gb == cap:
            matched_config = config
            break

    if not matched_config:
        return None

    # Total modules required vs what the listing provides in a single unit.
    # E.g., if target needs 8 modules, and listing is a 1-module kit, we need 8 units.
    # If target needs 8 modules, and listing is a 4-module kit, we need 2 units.
    # If target needs 8 modules, and listing is an 8-module kit, we need 1 unit.
    if matched_config.module_count % listing.kit_module_count != 0:
        # Listing doesn't evenly divide the requirement (e.g. need 8, kit is 3).
        # We can't trivially fulfill the exact target.
        return None

    units_needed = matched_config.module_count // listing.kit_module_count
    
    # If quantity is known and insufficient, we can't fulfill it.
    if listing.quantity_available is not None and listing.quantity_available < units_needed:
        return None

    # kit_price_usd is the total for the kit if it's a kit, else unit_price_usd.
    if listing.is_kit and listing.kit_price_usd is not None:
        unit_cost = listing.kit_price_usd
    else:
        unit_cost = listing.unit_price_usd
    return round(unit_cost * units_needed, 2)


def run_pipeline(
    listings: list[Listing], profile: Profile, qvl: QVL | None
) -> tuple[list[Listing], int]:
    """Run the validation pipeline on a set of listings.

    Returns:
        A tuple of (passed_listings, rejected_count).
    """
    passed: list[Listing] = []
    rejected_count = 0

    for listing in listings:
        # 1. Filters (reject non-compliant)
        rejection_reason = apply_filters(listing, profile.spec_filters, profile)
        if rejection_reason is not None:
            # Drop the listing
            rejected_count += 1
            continue

        # 2. Annotate QVL
        annotate_qvl(listing, qvl)

        # 3. Apply Flags
        apply_flags(listing, profile.spec_flags)

        # 4. Calculate total for target
        listing.total_for_target_usd = _calculate_total(listing, profile)
        
        # If unknown quantity and total_for_target_usd couldn't be calculated
        # we still keep it, but it might be flagged.
        if listing.quantity_available is None and "unknown_quantity" not in listing.flags:
            listing.flags.append("unknown_quantity")

        passed.append(listing)

    return passed, rejected_count
