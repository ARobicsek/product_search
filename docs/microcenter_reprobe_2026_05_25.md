# microcenter.com `known_failure` re-verification — 2026-05-25 (Phase 27 D3)

**Purpose:** Phase 26 / STRESS_TEST_26.md § Defect 3 flagged the
`microcenter.com` `known_failure` entry as possibly stale — stress26-mc's
Ryzen 7 9700X detail URL extracted cleanly (`microcenter.com | ok | 1 | 1`
at `$279.99`). Phase 27 brief D3 mandates re-probing ≥3 distinct microcenter
URLs at the registry defaults (`country: us, min_tier: 3, wait_condition:
networkidle`) before flipping the registry, in case the Phase 26 success was
a Cloudflare cache hit.

**Decision matrix (from Phase 27 brief):**
- ≥2 of 3 succeed → DOWNGRADE `severity: blocker` → `warning`.
- 3 of 3 succeed → REMOVE the `known_failure` block.
- 0 or 1 of 3 succeed → KEEP the block; add a note that it was re-checked.

## Probes (2026-05-25, ALTERLAB at registry defaults via `cli probe-url --render --detail`)

| # | Category | URL | Origin | Body | Outcome |
|---|---|---|---|---|---|
| 1 | CPU | https://www.microcenter.com/product/682199/amd-ryzen-7-9700x-granite-ridge-am5-380ghz-8-core-boxed-processor-heatsink-not-included | 200 | 39 chars | **FAIL** — empty-stub / challenge response |
| 2 | SSD | https://www.microcenter.com/product/660429/samsung-990-pro-2tb-samsung-v-nand-3-bit-mlc-pcie-gen-4-x4-nvme-m2-internal-ssd | 200 | 32,114 chars | **FAIL** — Cloudflare challenge body, 0 listings |
| 3 | Motherboard | https://www.microcenter.com/product/684469/asus-x870e-e-rog-strix-gaming-wifi-amd-am5-atx-motherboard | 200 | 32,030 chars | **FAIL** — Cloudflare challenge body, 0 listings |

All three runs used the registry-applied options
`{country: us, min_tier: 3, wait_condition: networkidle, render_js: True}` —
i.e. exactly the runtime path the worker would take. Each was a `--detail`
probe through the Tier 1.5 extractor, which reported `0 priced products`
on all three.

The ~32KB "200 OK" bodies are Cloudflare interstitial pages (the
"Just a moment..." challenge — same signature observed for backmarket at
tier 3+networkidle in Phase 24 / ADR-082). The 39-char body on probe #1 is
either a stub from a request the proxy could not complete or a JS-only
loading shell that `networkidle` couldn't settle into a real page.

## Verdict

**0 of 3 succeeded → KEEP the `known_failure` block** (per the brief's
0-or-1 rule). The Phase 26 stress26-mc success was a single cache-hit
outlier, not evidence the vendor is recoverable today. The block stays at
`severity: blocker` with a freshness note added so a future session knows
the entry was re-verified, not forgotten.

## Outcome

`worker/src/product_search/vendor_quirks.yaml` updated with a re-verification
note dated 2026-05-25; `web/lib/onboard/{promptText.ts, vendor-quirks-data.ts}`
regenerated via `node web/scripts/sync-prompt.js`. Defect 3 is closed but the
underlying bypass remains UNSOLVED — same as the PROGRESS "Noticed but
deferred / microcenter.com Cloudflare bypass" line item.
