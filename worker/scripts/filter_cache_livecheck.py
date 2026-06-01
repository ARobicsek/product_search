"""Phase 39 / ADR-142+143 live check — measure whether the ai_filter cache engages.

Runs the REAL ``product_search.validators.ai_filter.ai_filter`` against the real
Anthropic API (no monkeypatching of the LLM seam — that's the whole point) over a
multi-batch listing set. With caching, batch 1 would *write* the rules system
block to the ephemeral cache and batch 2 *read* it. Reports the real
``cache_read_input_tokens``/``cache_creation_input_tokens`` the SDK returned and
contrasts the cache-aware cost with the pre-ADR-142 (every-batch-full-price)
baseline reconstructed from the same token counts.

FINDING (ADR-143, 2026-06-01): on the heaviest committed profile
(``ddr5-rdimm-256gb``) the filter's system block is only ~1,861 tokens — BELOW
Haiku 4.5's ~2048-token minimum cacheable prefix — so ``cache_control`` is a
graceful no-op and the cache does NOT engage (``cw=0 cr=0``). The plumbing is
nonetheless correct: a synthetic ≥2048-token system writes on batch 1 and reads
on batch 2. Per-batch input is dominated by the (uncacheable, sent-once) listing
payload, not the system block — so the ADR-142 "~40% from caching" estimate does
not hold. This script prints the measured system-prompt token count so the
no-op is self-evident.

Listings come from a committed Serper fixture (duplicated to span two batches);
the profile is a committed v1 fixture. No live scraping, no Serper/Amazon spend —
only the Haiku filter call (~$0.04).

    python scripts/filter_cache_livecheck.py
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WORKER = Path(__file__).resolve().parent.parent
REPO = WORKER.parent
FIXTURE_SERPER = WORKER / "tests" / "fixtures" / "serper" / "ddr5_rdimm_ecc_32gb.json"
PROFILES_DIR = WORKER / "tests" / "fixtures" / "profiles"
PROFILE_SLUG = "ddr5-rdimm-256gb"


def _load_anthropic_key() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env = REPO / ".env"
    if not env.exists():
        raise SystemExit("No ANTHROPIC_API_KEY in env and no root .env to read it from.")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
            return
    raise SystemExit("ANTHROPIC_API_KEY not found in root .env.")


def _price(raw: Any) -> float | None:
    if isinstance(raw, (int, float)):
        return float(raw)
    if raw is None:
        return None
    import re
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(raw).replace(",", ""))
    return float(m.group()) if m else None


def main() -> None:
    _load_anthropic_key()
    # Keep the per-product filter log out of the repo's reports/ tree.
    os.environ.setdefault("PRODUCT_SEARCH_REPORTS_DIR", str(WORKER / "data" / "filter_cache_check"))
    os.environ["PRODUCT_SEARCH_PRODUCTS_DIR"] = str(PROFILES_DIR)
    os.environ.pop("WORKER_USE_FIXTURES", None)

    from product_search.models import Listing
    from product_search.profile import load_profile
    from product_search.validators import ai_filter as af

    data = json.loads(FIXTURE_SERPER.read_text(encoding="utf-8"))
    raw = data.get("shopping", [])

    def adapt(r: dict[str, Any]) -> Listing:
        return Listing(
            source="serper_shopping",
            url=r.get("link") or "https://shopping.google.com/x",
            title=r.get("title") or "",
            fetched_at=datetime.now(tz=UTC),
            brand=None, mpn=None, attrs={},
            condition="unknown", is_kit=False, kit_module_count=1,
            unit_price_usd=_price(r.get("price")) or 0.0,
            kit_price_usd=None, quantity_available=None,
            seller_name=r.get("source") or "", seller_rating_pct=None,
            seller_feedback_count=None, ship_from_country=None,
        )

    # Duplicate so we span >1 batch (50/batch) and a real cache READ occurs.
    listings = [adapt(r) for r in raw] + [adapt(r) for r in raw]
    profile = load_profile(PROFILE_SLUG)
    print(f"Running REAL ai_filter on {len(listings)} listings "
          f"({(len(listings) + 49) // 50} batches of 50)...\n")

    # Thin passthrough: record the system block on batch 1 to measure its token
    # count (the cache-eligibility threshold), then delegate to the REAL call_llm
    # so caching behaviour is unchanged.
    captured: dict[str, str] = {}
    real_call_llm = af.call_llm

    def passthrough(**kw: Any) -> Any:
        captured.setdefault("system", str(kw.get("system", "")))
        return real_call_llm(**kw)

    af.call_llm = passthrough  # type: ignore[assignment]
    passed = af.ai_filter(listings, profile)
    af.call_llm = real_call_llm  # type: ignore[assignment]

    import anthropic
    sys_tok = anthropic.Anthropic().messages.count_tokens(
        model="claude-haiku-4-5",
        system=captured.get("system", ""),
        messages=[{"role": "user", "content": "x"}],
    ).input_tokens
    print(f"system-prompt tokens         : {sys_tok:>8,}  (Haiku min cacheable ~2048)")
    u = af.LAST_RUN_USAGE
    assert u is not None, "no usage recorded — did the call fail?"

    in_tok = u["input_tokens"]
    out_tok = u["output_tokens"]
    cr = u["cache_read_input_tokens"]
    cw = u["cache_creation_input_tokens"]

    from product_search.llm.pricing import estimate_cost_usd, format_cost_usd

    cached = estimate_cost_usd(
        "anthropic", "claude-haiku-4-5", in_tok, out_tok,
        cache_read_input_tokens=cr, cache_creation_input_tokens=cw,
    )
    # Pre-ADR-142 baseline: every batch paid full input price for the whole
    # system block, i.e. the cached read+write tokens were all fresh input.
    baseline = estimate_cost_usd(
        "anthropic", "claude-haiku-4-5", in_tok + cr + cw, out_tok,
    )

    print(f"=== {len(passed)}/{len(listings)} passed ===\n")
    print(f"input_tokens (uncached)      : {in_tok:>8,}")
    print(f"cache_creation_input_tokens  : {cw:>8,}  (write @1.25x)")
    print(f"cache_read_input_tokens      : {cr:>8,}  (read  @0.10x)")
    print(f"output_tokens                : {out_tok:>8,}")
    print()
    print(f"cache-aware cost (ADR-142)   : {format_cost_usd(cached)}")
    print(f"pre-ADR-142 baseline cost    : {format_cost_usd(baseline)}")
    if cached is not None and baseline is not None and baseline > 0:
        print(f"reduction                    : {(1 - cached / baseline) * 100:.1f}%")
    print()
    print("CACHE ENGAGED" if cr > 0 else "!! NO CACHE READ — cache did NOT engage")


if __name__ == "__main__":
    main()
