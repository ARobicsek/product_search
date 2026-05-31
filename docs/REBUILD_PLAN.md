# Rebuild Plan — Serper recall layer + Haiku filter (Phase 31+)

**Status:** DRAFT for owner sign-off (2026-05-30). Companion ADR: ADR-133 (PROPOSED until sign-off).
**Read first:** PROGRESS.md, NEXT_SESSION_REBUILD_PLANNING.md, ADR-130/131/132, STRESS_TEST_30.md.
**This is a plan. No production code until signed off.**

---

## 0. Anchoring principle

> **Ship the simplest honest thing now; architect every seam for the rich future.**

v1 uses **Serper shopping data only** (+ the eBay API), **Google Shopping buy-links**, and **no Amazon**.
Three richer capabilities are *designed-for extension points*, not v1 work:

| Future capability | Why deferred | Seam that makes it drop-in later |
|---|---|---|
| **Type-aware detail verification** (stock count, color/size from a detail page) | Serper has no structured spec/stock field; detail-fetch reintroduces a (bounded) scraping surface | `attrs` dict on `Listing` + an optional post-filter "enrich top-N" pipeline step that fills `attrs`/`quantity_available` for the displayed set only |
| **Direct merchant buy-links** | Serper's `link` is always a `google.com/search?` cluster redirect | `Listing.buy_url` is a distinct field; a resolver step can rewrite it without touching anything upstream |
| **Amazon coverage** | Amazon US is absent from Google Shopping | Recall is a list of source adapters behind one `fetch(query)->[Listing]` seam; an Amazon adapter is just one more entry |

Owner's four answered forks (2026-05-30 interview) that set this:
1. **Verify depth:** Serper data only for now; build assuming a later **type-aware** verification tier.
2. **Rebuild scope:** **Keep core, redesign edges.**
3. **Amazon:** **Defer** (assume a later Amazon-capable tool); **use the eBay API now.**
4. **Buy link:** **Google Shopping link now**; build assuming an optional merchant link later.

---

## 1. Requirements traceability (owner's vision → how this plan covers it)

| Owner requirement | Covered by | v1 honesty caveat |
|---|---|---|
| Specify item, very specific (model/color/flavor) **or** loose (just the name) | Onboarder → `queries` + `match.aliases` + `title_excludes` + `match.variant_strict` (strict for specific asks, family for loose) | — |
| Specify vendors **or** "find a wide range / 10 best / 50 best" | `vendor_allowlist`/`vendor_blocklist` (post-filter on recall) + `display.max_listings` breadth knob | — |
| Constraints: "only new", "must have 10 in stock" | `filters` (`condition_in`, `in_stock`, `min_quantity`) | `min_quantity` honored **only for eBay listings** (eBay API returns quantity); Serper listings show quantity "unknown" until the type-aware verification tier lands. Surfaced honestly, never guessed. |
| Specify search frequency | `schedule.cron` (Phase 17 editor, kept) | — |
| Alert when price below X first/ever, and/or when a vendor newly carries the item | `alerts` (Phase 17 price rules, kept) + **new** `new_vendor_carries` rule from the daily diff; iPhone push (`notify.py`, ADR-057) | "Newly carries" gets *easier* with Serper — a new vendor is just a new host in the survivor set |
| Easily-comprehended display: price + key chars, **relevant to object type** | `product_type` + type-aware `display.attrs`; synth renders only relevant + populated columns | Most spec attrs (color/size/stock) are "unknown" in v1 except where Serper/eBay carry them |
| Not dominated by one vendor (e.g. all 50 slots eBay) | Deterministic **per-vendor cap** in the selection step | — |
| Clear, actionable error info | Compact honest error taxonomy (§8) — far fewer classes than the scraping era | — |
| Desktop + mobile, iPhone PDA primary | Existing mobile-first React UI (kept); tested at 375px | — |

---

## 2. Architecture: keep / retire / new

### Keep (proven, ~70% — do not touch beyond generalization)
- **No-fabrication seam (ADR-001).** Every price/stock/attribute is a structured field a deterministic fetch produced. The LLM filters + synthesizes; it never invents a number.
- **`ai_filter`** — Haiku-4.5 @ `temperature=0` (ADR-132). Precision is perfect on title-only data.
- **Deterministic synth → JSON sidecar** (`reports/<slug>/<date>.json`, ADR-096). Synth LLM already retired.
- **Validator pipeline** (`validators/`) — pure profile-driven filters + flags.
- **Storage** (repo-as-DB): CSV history + committed JSON reports; price/stock history; diff in pure Python.
- **Scheduler** (GitHub Actions fan-out cron + on-demand dispatch).
- **Alerts** (`alerts.py`) + **iPhone push** (`notify.py`, ADR-057).
- **Next.js UI on Vercel** + the **LLM provider abstraction** (`llm/`).
- **Worker/web split stays** (Python worker on Actions does recall+filter+synth+alerts; Next.js on Vercel does UI + onboarder + on-demand dispatch). No consolidation — it works and the scheduler lives on Actions.

### Retire (the scraping treadmill)
- `adapters/universal_ai.py` cascade (AlterLab → Scrappey → curl_cffi → httpx).
- All bot-wall machinery in `vendor_quirks.yaml`: `use_scrappey`, `skip_alterlab`, `alterlab_known_good`, `force_detail_backup`, per-vendor `search_url_template`, embedded-state / parser-gap tiers.
- The onboarder's per-vendor probing, detail-URL backfill, save-time probe modal, known-failure routing, `sources_pending`.
- Associated tests/fixtures for the above (Phase 36 cleanup).

### New
- **`adapters/serper.py`** — `fetch(query)->[Listing]` via `POST google.serper.dev/shopping`. Maps `title/source/price/link/productId/imageUrl/rating/ratingCount`. Sets `buy_url` (Google Shopping cluster URL for now). Serper-aware `single_sku_url` handling (ADR-131 P0).
- **eBay wire-in** — adapter exists; needs credentials (`EBAY_CLIENT_ID/SECRET`), dispatch registration, committed fixtures, and lift-out of RAM-specific title parsing.
- **Price-sanity + ship-from/country gate** (new validator step, deterministic — ADR-131 P1).
- **Diversity / anti-domination selection** (per-vendor cap + breadth knob).
- **Type-aware display attribute layer** (`product_type` → relevant `display.attrs`).

---

## 3. Data model — generalize `Listing` off RAM

`worker/src/product_search/models.py` is RAM-flavored. Generalize:

- **Keep:** `source`, `url`, `title`, `fetched_at`, `brand`, `mpn`, `attrs` (the generalization seam), `condition`, `quantity_available` (None = unknown, never 0), `seller_name`, `seller_rating_pct`, `seller_feedback_count`, `ship_from_country`, `flags`, `total_for_target_usd`.
- **Rename:** `unit_price_usd` → `price_usd` (generic). Keep kit fields **optional / in `attrs`** — they're RAM-specific, not core.
- **Move into `attrs` or a typed extension:** `is_kit`, `kit_module_count`, `kit_price_usd`, `qvl_status` (RAM-only; don't belong in the core shape).
- **Add:** `buy_url` (click target, distinct from canonical `url`), `image_url`, `rating`, `rating_count` (Serper carries these; useful for display).

`attrs` is where the future type-aware verification tier writes recovered specs (color/size/stock), so no model change is needed when it lands.

---

## 4. Profile schema v2 — collapses to "query + spec"

Sourcing is now a query, so a profile is no longer a curated vendor list with URLs/`page_type`/`alterlab_options`. New shape:

```yaml
slug: dji-neo-2-motion-fly-more-combo
display_name: DJI Neo 2 Drone Motion Fly More Combo
description: ...
product_type: drone          # drives type-aware display + sensible default flags
target: { unit: count, amount: 1 }

queries:                     # what we send to the recall adapters
  - DJI Neo 2 Motion Fly More Combo
match:
  aliases: [Neo 2 Motion Fly More, CP.FP.00000273.01]   # distinctive carry-gate tokens
  title_excludes: [Drone Only, standard, RC-N3, used]
  variant_strict: true       # ADR-117: exact SKU (true) vs family breadth (false)
filters:                     # hard rejects — honored where data exists, else degrade honestly
  - condition_in: [new]
  - in_stock: true
  # - min_quantity: 10       # accepted; honored for eBay; "needs detail verification" for Serper
flags:                       # soft warnings, listing kept
  - low_seller_feedback: { rating_pct_below: 98, count_below: 50 }
sources:
  serper: { enabled: true, gl: us }
  ebay:   { enabled: true }  # onboarder sets per product (off for subscriptions/groceries)
vendor_allowlist: []         # optional — "only these vendors"
vendor_blocklist: []         # optional — "never eBay/Poshmark/etc"
display:
  max_listings: 20           # breadth knob ("10 best / 50 best")
  per_vendor_cap: 3          # anti-domination
  attrs: [price, condition, seller, seller_rating]  # type-relevant columns
schedule: { cron: "..." }    # Phase 17 editor (kept)
alerts:                      # Phase 17 rules (kept) + new vendor rule
  - price_below: { amount: 550, mode: first }   # first | ever
  - new_vendor_carries: true
```

Gone: `sources` as a vendor/URL list, `page_type`, `extra.alterlab_options`, `sources_pending`, per-vendor URL templates, `report_columns` (replaced by type-aware `display.attrs`).

---

## 5. Run pipeline (one daily run)

1. **Read profile.**
2. **Recall** — dispatch enabled adapters (Serper always; eBay if enabled) with `queries`; union + dedup → `[Listing]`.
3. **Pre-filter normalization (deterministic):**
   - Serper-aware URL handling — skip the `single_sku_url` "rejects `search?`" rule for serper sources (they are always offers, never search pages); set `buy_url` (ADR-131 P0).
   - **Price-sanity + ship-from gate** — MAD-vs-median outlier flag (drops the $67.20 "Fly More Combo" anomaly from ranking #1) + country/domain allowlist (drops/flags LATAM/foreign currency-converted offers). Deterministic, no-fabrication-safe (ADR-131 P1).
   - `vendor_allowlist`/`vendor_blocklist` filter.
4. **`ai_filter`** (Haiku-4.5 @ temp=0) — relevance + spec match on title (+ any structured fields). Family vs exact per `match.variant_strict`.
5. **Validator pipeline** — hard filters (`condition_in`, `in_stock` where known); flags. `min_quantity` honored for eBay (real qty), honest-degraded for Serper.
6. **Selection / ranking** — rank survivors by `price_usd` asc; apply `per_vendor_cap` + `max_listings`. **All** survivors persist to history; the cap applies only to the *displayed* set (so "N more from <vendor>" is available).
7. **Diff vs yesterday** (pure Python) — new, dropped, price-changed >X%, **new vendor carrying** (new host in survivors).
8. **Deterministic synth → JSON sidecar** — type-aware columns (only populated + type-relevant attrs), bottom-line, diagnostics block.
9. **Alerts** — evaluate `price_below` (first/ever) + `new_vendor_carries` → push (ADR-057).
10. **Storage** — CSV + committed JSON report.

---

## 6. Onboarding v2 (massively simplified)

**Gone:** per-vendor curation, per-URL probing, detail-URL backfill, bot-wall routing, save-time probe modal, `sources_pending`, the whole AlterLab/Scrappey decision tree.

**New:** a conversational intake that produces a profile v2. The onboarder's job is to elicit and infer:
- **The item** — specific (model/color/flavor) vs loose. Produces good `queries`, `match.aliases`, `title_excludes`, and sets `match.variant_strict` (strict when the user is specific; family when loose). This directly serves "extremely specific … or less specific" from the vision.
- **`product_type`** — infer it → seed type-relevant `display.attrs` + sensible default `flags`.
- **Sources** — Serper always; `ebay.enabled` recommended on for electronics/collectibles, off for subscriptions/groceries (user can override).
- **Vendors / breadth** — capture `vendor_allowlist` if the user named vendors, or set `display.max_listings` from "10 best / 50 best".
- **Filters** — "only new" → `condition_in:[new]`; "in stock" → `in_stock`; "must have N" → `min_quantity` (with the honest eBay-only caveat surfaced).
- **Schedule + alerts** (Phase 17 editor flows, reused).
- **One live-preview step** — fire **one** real Serper query during onboarding so the user (and the model) can confirm the query + match spec actually surface the item before saving. This single cheap honest preview replaces the entire probe apparatus.

---

## 7. Display (type-aware, anti-domination, mobile-first)

- **Type-aware columns** — render an attr column only if it is (a) relevant to `product_type` **and** (b) populated for ≥1 shown listing. A subscription shows term/price/vendor; a drone shows price/condition/seller; never "color" for a magazine.
- **Anti-domination** — `per_vendor_cap` enforced in selection; a capped vendor shows a "N more from <vendor>" affordance.
- **Ranked cheapest-first** — price is the point of the tool; the price-sanity gate prevents anomalies from ranking #1. Reputation/condition are tiebreaks + flags.
- **Mobile-first** — existing UI; verified at 375px iPhone viewport before any UI task is called done.

---

## 8. Honest error taxonomy (replaces the per-vendor scraping diagnostics)

Recall via an index collapses errors to a small, clear set:

| Class | Trigger | User-facing message (actionable) |
|---|---|---|
| `index_unavailable` | Serper API error / rate-limited | "Couldn't reach the shopping index this run; the next scheduled run retries automatically." |
| `no_recall` | Serper + eBay returned 0 for the query | "No offers found — the query may be too specific, or this item isn't sold online. Edit the search query." |
| `all_filtered` | recall > 0, survivors = 0 | "Found N offers but none matched your spec — your match/filters may be too strict, or the query is mis-scoped. [filter diagnostics]" |
| `ebay_unavailable` | eBay auth/API error | "eBay was unavailable this run; other sources still ran." |
| `degraded_attr` | a filter needed an attr the index can't supply (`min_quantity` on Serper) | "Stock count isn't available from the index; showing offers without quantity verification." |

The existing rich filter-log (`ai_filter` per-verdict mirror) still backs `all_filtered`.

---

## 9. The three ADR-131 prerequisites + the temperature fix

- **P0 — `single_sku_url` Serper-aware + buy-link** → §5.3 (Serper adapter sets `buy_url` = Google Shopping link; URL validator skips the search-reject for serper sources). **Decision baked in:** ship the Google Shopping link now; merchant resolution is a future `buy_url` rewrite (no upstream change).
- **P1 — price-sanity + ship-from gate** → §5.3 (new deterministic validator step).
- **P1/P2 — title-only sub-precision / ADR-117 `variant_strict`** → §4 profile field. **Note (STRESS_TEST_30 Step 3b):** real Haiku/GLM already lean variant-STRICT. So default `variant_strict: true` and let the onboarder relax it to family breadth for loose requests.
- **Independent P0 one-liner (ADR-132):** thread `temperature=0` through the LLM call — `_anthropic.py`/`_openai.py` `call()` don't expose it today; at provider-default (~1.0) the filter is a run-to-run lottery.

---

## 10. Sequencing (one phase per session)

| Phase | Deliverable |
|---|---|
| **31** | Generalize `Listing`; profile v2 schema + loader; `adapters/serper.py`; Serper-aware URL handling; `temperature=0`. **Recall works end-to-end on Serper.** |
| **32** | Price-sanity/ship-from gate; diversity selection (per-vendor cap + breadth); type-aware display; honest error taxonomy. |
| **33** | eBay wire-in (credentials + fixtures + generalize parsing) + `vendor_allowlist`/`blocklist`. |
| **34** | Onboarder v2 (intake → profile v2 + one-shot Serper preview); retire probe apparatus. |
| **35** | Alerts `new_vendor_carries`; verify schedule editor + push end-to-end on the new pipeline. |
| **36** | Retire `universal_ai` + `vendor_quirks` bot-wall machinery + dead code/tests; doc cleanup. |
| *Future* | Type-aware detail verification; merchant-link resolution; Amazon adapter — each behind the §0 seams. |

---

## 11. Decisions — owner sign-off 2026-05-30 (all confirmed)

1. **`variant_strict` default** — ✅ **Onboarder decides per product** from how specific the request is (strict for model/color/flavor; family for a loose name). Global default `true`. (Real Haiku already leans strict — STRESS_TEST_30 Step 3b.)
2. **eBay scope** — ✅ **Onboarder decides per product** (`sources.ebay.enabled`): on for electronics/collectibles/apparel, off for subscriptions/groceries/services; user can override.
3. **Breadth / anti-domination** — ✅ **Cheapest-first + per-vendor cap**: default `max_listings: 20`, `per_vendor_cap: 3`, with a "N more from <vendor>" affordance; both editable per product ("10 best / 50 best"). Reputation/condition are tiebreaks + flags.
4. **Phase boundaries** — Phase 31 as scoped in §10 is the planned next session (owner to confirm "go" at session start).
