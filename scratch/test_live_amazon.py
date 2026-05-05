"""Quick test: run the extractor on a live Amazon body and see what prices we get."""
import sys
sys.path.insert(0, "worker/src")

from pathlib import Path
from product_search.adapters import universal_ai

body_path = Path("worker/worker/tests/fixtures/universal_ai/amazon-breville-live-2026-05-04.html")
html = body_path.read_text(encoding="utf-8")
base_url = "https://www.amazon.com/s?k=breville+barista+express"

cands = universal_ai._extract_candidates(html, base_url=base_url)

# Find the three Breville ASINs
target_asins = {"B00CH9QWOU": "BES870XL", "B0BBYNPV33": "BES876BSS Impress", "B00DS4767K": "BES870BSXL"}
for c in cands:
    for asin, name in target_asins.items():
        if asin in c["href"] and "/dp/" in c["href"]:
            print(f"\n{name} (ASIN {asin}):")
            print(f"  anchor_text: {c['anchor_text'][:80]}")
            print(f"  price_hints: {c['price_hints']}")
            print(f"  context len: {len(c['context'])}")
            # Also test the helper directly
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            for a in tree.css("a"):
                href = (a.attributes.get("href") or "")
                if asin in href and "/dp/" in href:
                    az_price = universal_ai._amazon_card_primary_price(a)
                    print(f"  _amazon_card_primary_price: {az_price}")
                    break
            break

print(f"\nTotal candidates: {len(cands)}")
