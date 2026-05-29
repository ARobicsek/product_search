"""Phase 30 spike — throwaway Serper.dev shopping recall script.

NOT wired into the pipeline. A scratch tool for the recall-layer go/no-go:
hit ``POST https://google.serper.dev/shopping`` and map each result to a
partial ``Listing``-shaped dict so we can eyeball coverage and (in the
companion ``serper_filter_runtest.py``) feed the results through the REAL
``ai_filter``.

Architectural commitment (ARCHITECTURE.md / CLAUDE.md): every price/seller/url
comes from Serper's STRUCTURED fields. The LLM is never asked to read a page or
emit a number. Missing fields stay ``None`` ("missing stays missing").

Usage::

    python scripts/serper_spike.py "DJI Neo 2 Motion Fly More Combo"
    python scripts/serper_spike.py "DDR5 RDIMM ECC 32GB" --save tests/fixtures/serper/ddr5.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"


def _load_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        # Mirror cli.py: load worker/.env for local runs.
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("SERPER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        sys.exit("SERPER_API_KEY not set (env or worker/.env).")
    return key


def serper_shopping(query: str, gl: str = "us", num: int = 40) -> dict[str, Any]:
    """POST to the Serper shopping endpoint; return the full parsed JSON."""
    body = json.dumps({"q": query, "gl": gl, "num": num}).encode()
    req = urllib.request.Request(
        SERPER_SHOPPING_URL,
        data=body,
        headers={"X-API-KEY": _load_key(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read())


_PRICE_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def parse_price(raw: Any) -> float | None:
    """Parse Serper's ``"$1,234.00"`` (or float) into a float; None if absent."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _PRICE_RE.search(str(raw).replace(",", ""))
    return float(m.group()) if m else None


def to_listing_dict(result: dict[str, Any]) -> dict[str, Any]:
    """Map one Serper shopping result to a partial Listing-shaped dict.

    Crude on purpose — this is a spike. Fields Serper doesn't carry stay None.
    """
    return {
        "source": result.get("source"),       # the merchant/store name
        "url": result.get("link"),
        "title": result.get("title"),
        "price": parse_price(result.get("price")),
        "seller_name": result.get("source"),
        "condition": None,                      # Serper rarely carries condition
        "rating": result.get("rating"),
        "ratingCount": result.get("ratingCount"),
        "delivery": result.get("delivery"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--gl", default="us")
    ap.add_argument("--num", type=int, default=40)
    ap.add_argument("--save", default=None, help="Write raw Serper JSON to this path (fixture).")
    args = ap.parse_args()

    data = serper_shopping(args.query, gl=args.gl, num=args.num)
    shopping = data.get("shopping", [])
    listings = [to_listing_dict(r) for r in shopping]

    print(f"query={args.query!r}  results={len(shopping)}  credits={data.get('credits')}\n")
    print(f"{'#':>2}  {'price':>9}  {'vendor':<22}  title")
    print("-" * 100)
    for i, ls in enumerate(listings):
        price = f"${ls['price']:,.2f}" if ls["price"] is not None else "—"
        vendor = (ls["seller_name"] or "?")[:22]
        title = (ls["title"] or "")[:55]
        print(f"{i:>2}  {price:>9}  {vendor:<22}  {title}")

    print("\nPer-vendor rollup:")
    roll = Counter(ls["seller_name"] or "?" for ls in listings)
    for vendor, n in roll.most_common():
        print(f"  {n:>3}  {vendor}")

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2))
        print(f"\nSaved raw Serper JSON -> {out}")


if __name__ == "__main__":
    main()
