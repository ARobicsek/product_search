"""Phase 30 spike — Step 3: run real Serper results through the REAL ai_filter.

Adapts a captured Serper shopping fixture into real ``Listing`` objects and runs
them through the *unmodified* ``product_search.validators.ai_filter.ai_filter``
against a fixture profile (loaded via ``PRODUCT_SEARCH_PRODUCTS_DIR`` — never a
live ``products/<slug>``; CLAUDE.md / ADR-062).

The ONLY thing intercepted is the network ``call_llm`` boundary: this container
has no Anthropic credential, so we (a) DUMP the exact production system prompt +
per-batch payload that ai_filter built, and (b) REPLAY a verdict JSON from a
sibling file. Every other line of ai_filter runs for real: prompt construction,
batching, ``_extract_json``, local→global index mapping, survivor selection,
filter-log writing. The replayed verdicts must be produced by applying the
dumped prompt's rules to the dumped payload (the filter-model's job).

Two-phase:
    # phase 1 — dump the request(s); exits asking for verdicts
    python scripts/serper_filter_runtest.py --fixture tests/fixtures/serper/ddr5_rdimm_ecc_32gb.json \
        --slug ddr5-rdimm-256gb --products-dir tests/fixtures/profiles
    # (write data/serper_spike/<slug>.batchNN.response.json)
    # phase 2 — same command; now it replays the verdicts and reports recall/precision
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.llm import LLMResponse
from product_search.models import Listing
from product_search.profile import load_profile

WORK = Path(__file__).resolve().parent.parent / "data" / "serper_spike"


def _price(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    import re
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(raw).replace(",", ""))
    return float(m.group()) if m else None


def adapt(result: dict[str, Any], *, rewrite_urls: bool = False) -> Listing:
    """Serper shopping result -> real Listing. Unknown fields stay None.

    Crucially attrs={} — we do NOT parse specs from the title; the filter must
    infer them from the title string, exactly as it will in production where
    Serper carries no structured spec fields. ``condition`` is typed ``str`` on
    Listing, so we store "unknown" (the architecture's "missing stays missing"
    intent; no rule fabricates a value).

    ``rewrite_urls``: Serper's ``link`` is ALWAYS a ``google.com/search?...``
    shopping redirect (no direct-merchant field exists), which trips the
    ``single_sku_url`` rule (rejects any URL containing ``search?``). With this
    flag set we normalize the link to a product-detail-shaped URL keyed by
    ``productId`` — representing what a migration adapter would do — so the run
    isolates product-MATCH precision from the (separately-reported)
    single_sku_url interaction.
    """
    price = _price(result.get("price"))
    link = result.get("link") or ""
    if rewrite_urls and result.get("productId"):
        link = f"https://shopping.google.com/product/{result['productId']}"
    return Listing(
        source="serper_shopping",
        url=link,
        title=result.get("title") or "",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs={},
        condition="unknown",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=price if price is not None else 0.0,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=result.get("source") or "",
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--products-dir", required=True)
    ap.add_argument("--rewrite-urls", action="store_true",
                    help="Normalize Serper google-redirect links to product-detail URLs.")
    ap.add_argument("--tag", default="", help="Suffix for request/response filenames (run A vs B).")
    args = ap.parse_args()

    os.environ["PRODUCT_SEARCH_PRODUCTS_DIR"] = str(Path(args.products_dir).resolve())
    # keep the per-product filter log out of the repo's reports/ tree
    os.environ.setdefault("PRODUCT_SEARCH_REPORTS_DIR", str(WORK / "reports"))
    WORK.mkdir(parents=True, exist_ok=True)

    data = json.loads(Path(args.fixture).read_text())
    listings = [adapt(r, rewrite_urls=args.rewrite_urls) for r in data.get("shopping", [])]
    profile = load_profile(args.slug)
    print(f"Loaded profile {profile.slug!r}; adapted {len(listings)} Serper listings.\n")

    # --- intercept the network boundary only -------------------------------
    import product_search.validators.ai_filter as af

    batch_no = {"n": 0}

    def fake_call_llm(*, provider: str, model: str, system: str,
                      messages: list[Any], response_format: str = "text",
                      max_tokens: int = 2048) -> LLMResponse:
        batch_no["n"] += 1
        n = batch_no["n"]
        sfx = f".{args.tag}" if args.tag else ""
        req = WORK / f"{args.slug}{sfx}.batch{n:02d}.request.json"
        resp = WORK / f"{args.slug}{sfx}.batch{n:02d}.response.json"
        req.write_text(json.dumps(
            {"system": system, "payload": json.loads(messages[0].content)}, indent=2
        ))
        if resp.exists():
            print(f"[replay] batch {n}: returning verdicts from {resp.name}")
            return LLMResponse(provider=provider, model=model,
                               text=resp.read_text(), input_tokens=0, output_tokens=0)
        raise SystemExit(
            f"\n[dump] Wrote request -> {req}\n"
            f"       Produce verdicts by applying that system prompt's rules to "
            f"the payload, save as -> {resp}\n"
            f"       (shape: {{\"evaluations\":[{{\"index\":N,\"pass\":bool,\"reason\":\"...\"}}]}})\n"
            f"       then re-run this exact command.\n"
        )

    af.call_llm = fake_call_llm  # type: ignore[assignment]

    passed = af.ai_filter(listings, profile)

    # --- report -------------------------------------------------------------
    print(f"\n=== RESULT: {len(passed)}/{len(listings)} listings passed the filter ===\n")
    print(f"{'idx':>3} {'verdict':>7}  {'price':>9}  {'vendor':<20}  title")
    print("-" * 110)
    for e in af.LAST_RUN_LOG:
        v = "PASS" if e.get("pass") else "fail"
        price = f"${e['price']:,.2f}" if isinstance(e.get("price"), (int, float)) else "—"
        title = (e.get("title") or "")[:48]
        # vendor isn't in the log entry; pull from listing by index
        idx = e.get("index", -1)
        vendor = listings[idx].seller_name[:20] if 0 <= idx < len(listings) else "?"
        print(f"{idx:>3} {v:>7}  {price:>9}  {vendor:<20}  {title}")
        if not e.get("pass"):
            print(f"            ↳ {e.get('reason','')[:95]}")


if __name__ == "__main__":
    main()
