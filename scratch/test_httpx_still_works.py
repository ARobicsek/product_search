"""Verify the httpx body (US-facing) still works after the fix."""
import sys
sys.path.insert(0, "worker/src")

from pathlib import Path
from product_search.adapters import universal_ai

body_path = Path("worker/tests/fixtures/universal_ai/amazon-breville-live-2026-05-04.html")
html = body_path.read_text(encoding="utf-8")
base_url = "https://www.amazon.com/s?k=breville+barista+express"

cands = universal_ai._extract_candidates(html, base_url=base_url)

target_asins = {"B00CH9QWOU": "BES870XL", "B0BBYNPV33": "BES876BSS Impress", "B00DS4767K": "BES870BSXL"}
for c in cands:
    for asin, name in target_asins.items():
        if asin in c["href"] and "/dp/" in c["href"]:
            print(f"  {name}: price_hints={c['price_hints']}")
            break

print(f"\nTotal: {len(cands)} candidates, {sum(1 for c in cands if c['price_hints'])} with prices")
