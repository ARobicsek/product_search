"""QVL annotation for the validator pipeline."""

from __future__ import annotations

from product_search.models import Listing
from product_search.profile import QVL


def annotate_qvl(listing: Listing, qvl: QVL | None) -> None:
    """Determine and set the `qvl_status` for a listing.

    Supported statuses (Phase 3):
    - "qvl": Exact MPN match in the QVL list.
    - "unknown": No exact match, or missing MPN/Brand, or no QVL provided.

    (Incompatible logic is deferred to a future phase).
    """
    if not qvl or not qvl.qvl:
        listing.qvl_status = "unknown"
        return

    if not listing.mpn:
        listing.qvl_status = "unknown"
        return

    mpn = listing.mpn.lower().strip()

    # Look for exact MPN match
    for entry in qvl.qvl:
        if entry.mpn.lower().strip() == mpn:
            listing.qvl_status = "qvl"
            return

    listing.qvl_status = "unknown"
