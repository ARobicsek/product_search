"""Phase 41 / ADR-146 — offline demonstration of the deterministic alias pre-pass.

Reproducible WITHOUT the home box or any API key: loads the committed real
134-listing recall for ``sk-hynix-hmcg84agbra191n-ddr5-32gb`` and runs the exact
production pre-pass (``partition_by_exact_alias``) to show every exact-MPN-in-title
listing being surfaced for free — the listings the relevance LLM dropped on the
live run (Haiku fabricated a "Refurbished" rejection on the exact part
``HMCG84AGBRA191N``; qwen-coder dropped 5 exact parts).

The model-vs-model survivor numbers live in ADR-146 (they need live calls); this
script pins the *deterministic* half, which is the load-bearing lever.

Usage:
    uv run python scripts/alias_prepass_compare.py
"""

# ruff: noqa: E501  (scratch demo script; wide title lines)
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from product_search.models import Listing
from product_search.profile_v2_filter import title_has_exact_alias, title_states_excluded_condition

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "filter_compare" / "sk-hynix-ram-134.json"

# From products/sk-hynix-hmcg84agbra191n-ddr5-32gb/profile.yaml (match.aliases + filters.condition_in).
ALIASES = ["HMCG84AGBRA191N", "M321R4GA3BB6-CWM", "M321R4GA3BB6-CWMK", "MTC20F1045S1RC56BD1", "KSM56R46BD4PMI-32HAI"]
CONDITION_IN: list[str] = []  # profile allows all conditions

_REPLACEMENT = re.compile(r"replacement for|compatible", re.IGNORECASE)
_FOR_PARTS = re.compile(r"\bfor parts\b", re.IGNORECASE)


def _to_listing(d: dict) -> Listing:
    return Listing(
        source=d.get("source") or "serper_shopping", url=d.get("url") or "", title=d.get("title") or "",
        fetched_at=datetime.now(tz=UTC), brand=None, mpn=None, attrs=d.get("attrs") or {},
        condition=d.get("condition") or "", is_kit=False, kit_module_count=1,
        unit_price_usd=d.get("price"), kit_price_usd=None, quantity_available=None,
        seller_name=None, seller_rating_pct=None, seller_feedback_count=None, ship_from_country=None,
    )


def main() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    listings = [_to_listing(d) for d in data["listings"]]
    hits = [
        lst for lst in listings
        if title_has_exact_alias(lst.title, ALIASES) and not title_states_excluded_condition(lst.title, CONDITION_IN)
    ]
    print(f"n={len(listings)}  exact-alias hits auto-passed (zero LLM cost) = {len(hits)}\n")
    for lst in hits:
        tag = ""
        if _FOR_PARTS.search(lst.title):
            tag = "  [FOR PARTS — broken; title_excludes it to drop]"
        elif _REPLACEMENT.search(lst.title):
            tag = "  [3rd-party replacement — in-scope for the 'Compatible Equivalents' profile]"
        print(f"  ${str(lst.unit_price_usd):>9}  {lst.title[:74]}{tag}")


if __name__ == "__main__":
    main()
