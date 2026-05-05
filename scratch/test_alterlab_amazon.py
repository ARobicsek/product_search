"""Check all anchors with prices and what the LLM would see for these ASINs."""
import sys, json
sys.path.insert(0, "worker/src")

from pathlib import Path
from product_search.adapters import universal_ai

body_path = Path("worker/tests/fixtures/universal_ai/amazon-breville-alterlab-2026-05-04.html")
html = body_path.read_text(encoding="utf-8")
base_url = "https://www.amazon.com/s?k=breville+barista+express"

cands = universal_ai._extract_candidates(html, base_url=base_url)

target_asins = {"B00CH9QWOU": "BES870XL", "B0BBYNPV33": "BES876BSS Impress", "B00DS4767K": "BES870BSXL"}

# Show ALL candidates that reference these ASINs
print("=== Candidates for target ASINs ===")
for c in cands:
    for asin, name in target_asins.items():
        if asin in c["href"]:
            print(f"\n  [{c['idx']}] {name}")
            print(f"      href: ...{c['href'][-60:]}")
            print(f"      anchor_text: {c['anchor_text'][:80]}")
            print(f"      price_hints: {c['price_hints']}")
            print(f"      context ({len(c['context'])} chars): {c['context'][:200]}")
            break

# Also show candidates with prices to see what the LLM payload looks like
print("\n\n=== All candidates with price hints ===")
for c in cands:
    if c["price_hints"]:
        print(f"  [{c['idx']}] {c['anchor_text'][:60]:60s} {c['price_hints']}")

print(f"\nTotal candidates: {len(cands)}")
print(f"With prices: {sum(1 for c in cands if c['price_hints'])}")
print(f"Without prices: {sum(1 for c in cands if not c['price_hints'])}")
