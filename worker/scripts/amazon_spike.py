"""Phase 37 Step-1 spike — throwaway DataForSEO Amazon recall script.

NOT wired into the pipeline. A scratch tool for the Amazon recall GO/NO-GO
(ADR-141 / PHASES.md "Phase 37"): hit the DataForSEO Merchant Amazon Products
LIVE Advanced endpoint and map each result to a partial ``Listing``-shaped dict
so we can eyeball recall + structured-field completeness before building the
real ``adapters/amazon.py``.

Architectural commitment (ARCHITECTURE.md / CLAUDE.md): every price/asin/url
comes from DataForSEO's STRUCTURED fields. The LLM is never asked to read a
page or emit a number. Missing fields stay ``None`` ("missing stays missing").

Auth: HTTP Basic with ``DATAFORSEO_LOGIN`` / ``DATAFORSEO_PASSWORD`` (env, then
repo-root ``.env``, then ``worker/.env``).

Usage::

    python scripts/amazon_spike.py "DJI Neo 2 Motion Fly More Combo"
    python scripts/amazon_spike.py "q1" "q2" "q3" --save-dir tests/fixtures/amazon
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ENDPOINT = "https://api.dataforseo.com/v3/merchant/amazon/products/live/advanced"
PRODUCT_TYPES = {"amazon_serp", "amazon_paid"}


def _load_creds() -> tuple[str, str]:
    import os

    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if login and password:
        return login, password

    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / ".env",  # repo root
        here.parent.parent / ".env",          # worker/.env
    ]
    found: dict[str, str] = {}
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            for key in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"):
                if line.startswith(key + "=") and key not in found:
                    found[key] = line.split("=", 1)[1].strip()
        if "DATAFORSEO_LOGIN" in found and "DATAFORSEO_PASSWORD" in found:
            break
    login = login or found.get("DATAFORSEO_LOGIN", "")
    password = password or found.get("DATAFORSEO_PASSWORD", "")
    if not (login and password):
        sys.exit("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set (env or .env).")
    return login, password


def amazon_products(keyword: str, *, depth: int = 100) -> dict[str, Any]:
    """POST one keyword to the Amazon Products LIVE Advanced endpoint."""
    login, password = _load_creds()
    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    body = json.dumps(
        [
            {
                "keyword": keyword,
                "location_name": "United States",
                "language_name": "English (United States)",
                "se_domain": "amazon.com",
                "depth": depth,
            }
        ]
    ).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _product_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = data.get("tasks") or []
    if not tasks:
        return []
    result = tasks[0].get("result") or []
    if not result:
        return []
    items = result[0].get("items") or []
    return [it for it in items if it.get("type") in PRODUCT_TYPES]


def to_listing_dict(item: dict[str, Any]) -> dict[str, Any]:
    rating = item.get("rating") or {}
    return {
        "source": "amazon_dataforseo",
        "type": item.get("type"),
        "url": item.get("url"),
        "title": item.get("title"),
        "price": item.get("price_from"),
        "data_asin": item.get("data_asin"),
        "rating": rating.get("value"),
        "rating_count": rating.get("votes_count"),
        "image_url": item.get("image_url"),
        "is_amazon_choice": item.get("is_amazon_choice"),
    }


def _pct(n: int, total: int) -> str:
    return f"{(100 * n / total):.0f}%" if total else "—"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="+")
    ap.add_argument("--depth", type=int, default=100)
    ap.add_argument("--save-dir", default=None, help="Dump raw JSON per query here.")
    args = ap.parse_args()

    # Windows consoles default to cp1252 and choke on non-Latin chars in titles.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

    grand_total = 0
    grand_cost = 0.0
    for q in args.queries:
        try:
            data = amazon_products(q, depth=args.depth)
        except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
            print(f"\n### {q!r}: HTTP {exc.code} {exc.reason}\n{exc.read().decode()[:500]}")
            continue

        status = data.get("status_code")
        cost = float(data.get("cost") or 0.0)
        grand_cost += cost
        items = _product_items(data)
        grand_total += len(items)
        listings = [to_listing_dict(it) for it in items]

        with_price = sum(1 for x in listings if x["price"] is not None)
        with_asin = sum(1 for x in listings if x["data_asin"])
        with_url = sum(1 for x in listings if x["url"])
        with_rating = sum(1 for x in listings if x["rating"] is not None)
        n = len(listings)

        print(f"\n{'=' * 100}")
        print(f"QUERY: {q!r}   status={status}   products={n}   cost=${cost:.4f}")
        print(
            f"  field completeness: price {_pct(with_price, n)} | asin {_pct(with_asin, n)} | "
            f"url {_pct(with_url, n)} | rating {_pct(with_rating, n)}"
        )
        print(f"{'#':>2}  {'price':>9}  {'rating':>6}  {'asin':<12}  title")
        print("-" * 100)
        for i, ls in enumerate(listings[:25]):
            price = f"${ls['price']:,.2f}" if ls["price"] is not None else "—"
            rating = f"{ls['rating']}" if ls["rating"] is not None else "—"
            asin = (ls["data_asin"] or "?")[:12]
            title = (ls["title"] or "")[:50]
            tag = "*" if ls["type"] == "amazon_paid" else " "
            print(f"{i:>2}{tag} {price:>9}  {rating:>6}  {asin:<12}  {title}")
        if n > 25:
            print(f"   ... +{n - 25} more")

        if args.save_dir:
            slug = "".join(c if c.isalnum() else "_" for c in q.lower())[:40]
            out = Path(args.save_dir) / f"{slug}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"  saved raw JSON -> {out}")

    print(f"\n{'=' * 100}")
    print(f"TOTAL products across {len(args.queries)} queries: {grand_total}   total cost: ${grand_cost:.4f}")
    print("(* = sponsored/amazon_paid)")


if __name__ == "__main__":
    main()
