"""Structured JSON sidecar for v2 (Serper-recall) daily reports (Phase 32).

The React UI reads ``reports/<slug>/<date>.json`` (ADR-096). This builds the v2
payload deterministically from the selected display set — no LLM, no fabricated
values (ADR-001). It is a superset-compatible evolution of ``report_json.py``:
same ``slug``/``display_name``/``snapshot_date``/``generated_at``/``listings``/
``run_cost`` keys the UI already knows, plus the v2 additions
(``schema_version: 2``, type-aware ``columns``, anti-domination ``overflow``,
the honest ``outcome``, and the recall/survivor counts).

Phase 32 writes the sidecar; the UI's type-aware rendering of ``columns`` is a
Phase 34 concern. Keeping the shape stable now means the UI change is additive.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from product_search.models import Listing
from product_search.profile_v2 import ProfileV2
from product_search.run_outcome import RunOutcome
from product_search.selection import SelectionResult


def _listing_to_display(listing: Listing, rank: int) -> dict[str, Any]:
    """Map a Listing to the v2 UI display shape (generic, RAM-free)."""
    return {
        "rank": rank,
        "title": listing.title,
        "url": listing.url,
        "buy_url": listing.buy_url,
        "image_url": listing.image_url,
        "price_usd": listing.price_usd,
        "condition": listing.condition,
        "seller_name": listing.seller_name,
        "seller_rating_pct": listing.seller_rating_pct,
        "seller_feedback_count": listing.seller_feedback_count,
        "rating": listing.rating,
        "rating_count": listing.rating_count,
        "ship_from_country": listing.ship_from_country,
        "quantity_available": listing.quantity_available,
        "flags": listing.flags,
        "brand": listing.brand,
        "mpn": listing.mpn,
        "attrs": listing.attrs,
        "source": listing.source,
    }


def _build_run_cost(run_calls: list[dict[str, Any]]) -> dict[str, Any]:
    from product_search.llm.pricing import estimate_cost_usd

    total = 0.0
    any_unpriced = False
    calls_out: list[dict[str, Any]] = []
    for c in run_calls:
        cost = estimate_cost_usd(
            str(c.get("provider", "")),
            str(c.get("model", "")),
            c.get("input_tokens"),
            c.get("output_tokens"),
        )
        if cost is None:
            any_unpriced = True
        else:
            total += cost
        calls_out.append(
            {
                "step": c.get("step", "?"),
                "provider": c.get("provider", "?"),
                "model": c.get("model", "?"),
                "input_tokens": c.get("input_tokens") or 0,
                "output_tokens": c.get("output_tokens") or 0,
                "cost_usd": cost,
            }
        )
    return {"calls": calls_out, "total_usd": total, "any_unpriced": any_unpriced}


def build_v2_payload(
    *,
    profile: ProfileV2,
    selection: SelectionResult,
    all_survivors: list[Listing],
    columns: list[str],
    outcome: RunOutcome,
    recall_count: int,
    survivor_count: int,
    run_calls: list[dict[str, Any]],
    snapshot_date: date,
) -> dict[str, Any]:
    """Build the full v2 JSON sidecar payload.

    ``all_survivors`` is the complete ranked list (price-sorted, no vendor cap).
    The UI ships both ``listings`` (the capped display set) and ``all_listings``
    (every survivor) so it can offer progressive disclosure ("Show all N")
    without a round-trip.
    """
    listings_json = [
        _listing_to_display(lst, i + 1) for i, lst in enumerate(selection.displayed)
    ]
    all_listings_json = [
        _listing_to_display(lst, i + 1) for i, lst in enumerate(all_survivors)
    ]
    return {
        "schema_version": 2,
        "slug": profile.slug,
        "display_name": profile.display_name,
        "product_type": profile.product_type,
        "snapshot_date": snapshot_date.isoformat(),
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "columns": columns,
        "listings": listings_json,
        "all_listings": all_listings_json,
        "overflow": selection.overflow,
        "hidden_anomalies": selection.hidden_anomalies,
        "recall_count": recall_count,
        "survivor_count": survivor_count,
        "displayed_count": len(listings_json),
        "outcome": outcome.to_dict(),
        "run_cost": _build_run_cost(run_calls),
    }


def build_v2_markdown(payload: dict[str, Any]) -> str:
    """A lean human/legacy markdown fallback rendered from the payload.

    The JSON sidecar is the source of truth (ADR-096); this exists so a run
    still leaves a readable ``.md`` for anyone browsing the repo.
    """
    lines: list[str] = [f"# {payload['display_name']} — {payload['snapshot_date']}", ""]

    outcome = payload["outcome"]
    if outcome["class"] != "ok":
        lines.append("> [!NOTE]")
        lines.append(f"> {outcome['message']}")
        lines.append("")
    for note in outcome.get("notes", []):
        lines.append(f"_{note['message']}_")
    if outcome.get("notes"):
        lines.append("")

    listings = payload["listings"]
    if listings:
        lines.append(
            f"**{payload['displayed_count']} shown** "
            f"(of {payload['survivor_count']} matched, "
            f"{payload['recall_count']} found). Cheapest first."
        )
        lines.append("")
        columns = payload.get("columns", ["price", "title", "seller", "condition"])
        header_cols = ["#"]
        for c in columns:
            if c == "price" or c == "price_usd":
                header_cols.append("Price")
            elif c == "title":
                header_cols.append("Title")
            elif c == "seller" or c == "seller_name":
                header_cols.append("Vendor")
            elif c == "condition":
                header_cols.append("Condition")
            elif c == "seller_rating" or c == "seller_rating_pct":
                header_cols.append("Rating")
            else:
                header_cols.append(c.replace("_", " ").title())

        lines.append("| " + " | ".join(header_cols) + " |")
        lines.append("|" + "|".join(["---"] * len(header_cols)) + "|")
        for lst in listings:
            row = [str(lst.get("rank", ""))]
            for c in columns:
                if c == "price" or c == "price_usd":
                    price = lst.get("price_usd")
                    row.append(f"${price:,.2f}" if price else "—")
                elif c == "title":
                    title = str(lst.get("title", "")).replace("|", "\\|")[:80]
                    suspicious = "🚨 SUSPICIOUS: " if "price_anomaly_low" in lst.get("flags", []) else ""
                    url = lst.get("url", "")
                    title_link = f"[{title}]({url})" if url else title
                    row.append(f"{suspicious}{title_link}")
                elif c == "seller" or c == "seller_name":
                    row.append(str(lst.get("seller_name") or "—").replace("|", "\\|"))
                elif c == "condition":
                    row.append(str(lst.get("condition") or "—"))
                elif c == "seller_rating" or c == "seller_rating_pct":
                    rating = lst.get("seller_rating_pct")
                    row.append(f"{rating}%" if rating is not None else "—")
                else:
                    val = lst.get("attrs", {}).get(c)
                    row.append(str(val or "—").replace("|", "\\|"))
            lines.append("| " + " | ".join(row) + " |")
        if payload["overflow"]:
            lines.append("")
            for vendor, n in payload["overflow"].items():
                lines.append(f"_{n} more from {vendor}._")
    else:
        lines.append("_No listings to show._")

    return "\n".join(lines)
