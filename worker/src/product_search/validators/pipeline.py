"""Pipeline orchestration for the validator phase.

Runs filters, flags, QVL annotation, and calculates `total_for_target_usd`.
"""

from __future__ import annotations

import re

from product_search.models import Listing
from product_search.profile import QVL, Profile
from product_search.validators.ai_filter import ai_filter
from product_search.validators.flags import apply_flags
from product_search.validators.qvl import annotate_qvl


def infer_brand_from_title(listing: Listing, candidates: list[str]) -> None:
    """Fill in ``listing.brand`` from a title-substring match if it's None.

    eBay's Browse API doesn't always return ``brand`` in the search summary
    for non-RAM categories (e.g. headphones), so the synthesizer's Brand
    column shows "unknown" even for visually obvious brands. Match is
    case-insensitive and word-bounded so "boseheadphones" doesn't match
    "Bose". The first candidate found wins (so order the list by
    specificity if any candidates share a prefix).
    """
    if listing.brand or not candidates:
        return
    title_lower = listing.title.lower()
    for cand in candidates:
        token = cand.strip().lower()
        if not token:
            continue
        if re.search(rf"\b{re.escape(token)}\b", title_lower):
            listing.brand = cand
            return


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
    import json
    from datetime import UTC, datetime
    from typing import Any

    from product_search.validators import ai_filter as ai_filter_mod
    from product_search.validators.filters import apply_filters

    # 1. Deterministic Filters Pass
    deterministic_rejects: list[dict[str, Any]] = []
    survivors: list[Listing] = []
    survivors_indices: list[int] = []

    for idx, listing in enumerate(listings):
        reason = apply_filters(listing, profile.spec_filters, profile)
        if reason is not None:
            deterministic_rejects.append({
                "index": idx,
                "pass": False,
                "reason": reason,
                "title": listing.title,
                "price": listing.unit_price_usd,
                "url": listing.url,
                "source": listing.source,
            })
        else:
            survivors.append(listing)
            survivors_indices.append(idx)

    # 2. AI Filter Pass for Relevance
    ai_passed_listings: list[Listing] = []
    if survivors:
        ai_passed_listings = ai_filter(survivors, profile)
        
        # Map local survivor indices in ai_filter_mod.LAST_RUN_LOG back to original indices
        for entry in ai_filter_mod.LAST_RUN_LOG:
            local_idx = entry.get("index")
            if local_idx is not None and 0 <= local_idx < len(survivors_indices):
                entry["index"] = survivors_indices[local_idx]
        
        complete_log = deterministic_rejects + ai_filter_mod.LAST_RUN_LOG
    else:
        # No survivors reached AI filter: reset LAST_RUN_LOG/USAGE to show deterministic only
        ai_filter_mod.LAST_RUN_LOG = []
        ai_filter_mod.LAST_RUN_USAGE = None
        complete_log = deterministic_rejects

    # Sort log entries by original index to keep input order
    complete_log.sort(key=lambda x: x["index"])
    ai_filter_mod.LAST_RUN_LOG = list(complete_log)

    # Write the logs to daily and per-product log files without duplication
    timestamp = datetime.now(tz=UTC).isoformat()
    
    # Append deterministic rejects to daily filter log
    if deterministic_rejects:
        rows_daily = [
            json.dumps({"timestamp": timestamp, "product": profile.slug, **entry})
            for entry in deterministic_rejects
        ]
        try:
            with ai_filter_mod._filter_log_path().open("a", encoding="utf-8") as f:
                f.write("\n".join(rows_daily) + ("\n" if rows_daily else ""))
        except Exception:
            pass

    # Overwrite the per-product log with the complete set of logs (deterministic + AI)
    rows_complete = [
        json.dumps({"timestamp": timestamp, "product": profile.slug, **entry})
        for entry in complete_log
    ]
    try:
        per_product = ai_filter_mod._per_product_filter_log_path(profile.slug)
        if per_product is not None:
            with per_product.open("w", encoding="utf-8") as f:
                f.write("\n".join(rows_complete) + ("\n" if rows_complete else ""))
    except Exception:
        pass

    rejected_count = len(listings) - len(ai_passed_listings)
    passed: list[Listing] = []

    for listing in ai_passed_listings:
        # Infer brand from title if the adapter left it blank
        if profile.brand_candidates:
            infer_brand_from_title(listing, profile.brand_candidates)

        # Annotate QVL
        annotate_qvl(listing, qvl)

        # Apply Flags
        apply_flags(listing, profile.spec_flags)

        # Calculate total for target
        listing.total_for_target_usd = _calculate_total(listing, profile)

        # If unknown quantity and the target requires multi-unit fulfillment,
        # flag it as unknown_quantity.
        is_multi_unit_target = False
        if profile.target.configurations:
            cap = listing.attrs.get("capacity_gb")
            if cap is not None:
                for config in profile.target.configurations:
                    if config.module_capacity_gb == cap:
                        if listing.kit_module_count > 0 and config.module_count // listing.kit_module_count > 1:
                            is_multi_unit_target = True
                        break
        elif profile.target.amount > 1:
            if listing.kit_module_count > 0 and profile.target.amount // listing.kit_module_count > 1:
                is_multi_unit_target = True

        if is_multi_unit_target and listing.quantity_available is None and "unknown_quantity" not in listing.flags:
            listing.flags.append("unknown_quantity")

        passed.append(listing)

    return passed, rejected_count
