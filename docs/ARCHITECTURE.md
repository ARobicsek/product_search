# Architecture

## The premise

Conversational LLMs reliably fabricate prices, stock counts, and quotes when asked to find listings. Stricter prompts don't fix this — across four iterations of an explicit "paste verbatim quotes from the page proving stock" prompt, the model invented quotes that didn't exist in the page text. The fix is structural: the LLM never *claims* a number it didn't read from a real fetched page.

This system implements that structurally:

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Source adapters │───▶│  Validator       │───▶│  Storage         │
│  (per-source)    │    │  pipeline        │    │  (SQLite + CSV)  │
│  HTTP + parse    │    │  filter / flag   │    │  per-day rows    │
└──────────────────┘    └──────────────────┘    └──────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────┐
                                              │  Synthesizer     │
                                              │  (LLM, structured│
                                              │  data input only)│
                                              └──────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────┐
                                              │  Daily report    │
                                              │  (markdown,      │
                                              │  committed)      │
                                              └──────────────────┘
```

The LLM input is JSON the deterministic layer produced. The LLM output is markdown. There is no path by which a fabricated price reaches the user.

## Components

### Source adapters (`worker/src/product_search/adapters/`)

One file per source. Each exports `fetch(query: AdapterQuery) -> list[Listing]`. Heterogeneous sites, homogeneous output.

Adapter responsibilities:
- HTTP with a pinned, polite User-Agent and per-host rate limit.
- Parse to the shared `Listing` model.
- Never invent fields. If `quantity_available` isn't on the page, return `None`.
- Save raw response to a fixture path during dev runs (gated by env var).

The `AdapterQuery` type is generic — `{search_terms, capacity_filter, ...}` — so adapters don't bake in product-specific assumptions. Product-specific search strings come from the profile.

#### Who actually assembles the verified data?

A natural question, since the LLM is downstream of "verified data": *who assembles it, especially from sites without an API?*

**The adapter does, with explicit human-written code.** "Deterministic" here doesn't mean "every site has an API." It means the extraction logic is unambiguous, testable, and returns `None` for fields it can't read instead of guessing. Three tiers by mechanism:

| Tier | Mechanism | Examples | Adapter shape |
|---|---|---|---|
| 1 | Real API | eBay Browse API; Shopify storefronts (`/products/<handle>.json`, `/collections/<slug>/products.json`) | ~30 lines: HTTP call + map JSON to `Listing`. Most reliable. |
| 2 | Server-rendered HTML | most eBay seller pages, Newegg, ServerSupply, Memory.net | ~100-200 lines: `httpx` + `selectolax` with explicit CSS selectors. Adapter author saves an HTML fixture and writes selectors against it. |
| 3 | JS-rendered or anti-bot | some Cloudflare-protected stores | Playwright as a last resort, with per-source rate limit. If a source isn't worth the effort, we skip it. |

Two non-negotiables across all tiers:

1. **Fixtures are committed.** A test runs the parser against a saved fixture and asserts the resulting `Listing` shape. When the site's HTML changes, the test fails loudly — which is the correct behavior, much better than the silent fabrication an LLM "reader" would produce.
2. **Missing data stays missing.** If `quantity_available` isn't on the page, the adapter returns `None`, the validator may reject the listing for "stock unverified," and the report says "unknown." No guess ever travels through the pipeline.

The LLM is *never* the extractor. There is no path where the LLM "reads" a product page in this system. The synthesizer's input is always JSON the adapter built from a real fetch.

Phased adapter rollout:

- **Tier A** (high-signal, known sources): eBay (Browse API), NEMIX (Shopify JSON), CloudStorageCorp (eBay seller HTML), Mem-Store (eBay seller HTML).
- **Tier B**: Newegg, ServerSupply, Memory.net, The Server Store.
- **Tier C** (defer): Reddit r/homelabsales, B&H, CDW.

### Listing model (`worker/src/product_search/models.py`)

The same shape from every source. Sketch:

```python
@dataclass
class Listing:
    source: str                  # "ebay", "nemixram", ...
    url: str                     # direct product URL — never a search page
    fetched_at: datetime

    # Product (some fields product-type-specific; a generic `attrs: dict` covers the long tail)
    brand: str | None
    mpn: str | None
    attrs: dict[str, Any]        # capacity_gb, speed_mts, etc — keys defined per product profile

    # Listing
    condition: str               # "new", "used", "refurbished"
    is_kit: bool
    kit_module_count: int        # 1 if single, N if kit
    unit_price_usd: float
    kit_price_usd: float | None
    quantity_available: int | None  # None means "we don't know" — not 0

    # Seller
    seller_name: str
    seller_rating_pct: float | None
    seller_feedback_count: int | None
    ship_from_country: str | None

    # Set by validator pipeline
    qvl_status: str | None       # "qvl" | "inferred-compatible" | "unknown" | "incompatible"
    flags: list[str]
    total_for_target_usd: float | None  # computed for ranking
```

`attrs` is the generalization seam. RAM uses `capacity_gb`, `speed_mts`, `form_factor`, `ecc`, `voltage_v`, `rank`. GPUs would use `vram_gb`, `tdp_w`, `pcie_gen`. The profile declares which attrs are required; the validator enforces them.

### Validator pipeline (`worker/src/product_search/validators/`)

A chain of pure functions. Each takes `(Listing, Profile) -> Listing | None`. Returning `None` drops the listing. Each is testable in isolation against fixtures.

Two kinds:

- **Rejecters** (`filters.py`): hard fails. `reject_wrong_form_factor`, `reject_below_min_speed`, `reject_non_ecc`, `reject_search_page_url`, `reject_insufficient_quantity`, `reject_out_of_stock`.
- **Flaggers** (`flags.py`): warnings, listing kept. `flag_china_shipping`, `flag_low_seller_feedback`, `flag_smart_memory`, `flag_generic_brand`.

Rejecter and flagger rules come from the profile (`spec_filters`, `spec_flags`). The functions themselves are generic; the data is product-specific. This is how the same code handles RAM, GPUs, and anything else.

`compute_total_for_target` and `annotate_qvl_status` are also pipeline steps.

### Storage (`worker/src/product_search/storage/`)

SQLite as the canonical store. One row per `(listing_url, fetched_at)`. This gives free price-and-stock history per listing and makes the diff-vs-yesterday feature trivial.

A daily CSV per product per day is also written to `worker/data/<product>/<date>.csv` for inspection (gitignored). The committed artifact is the synthesized markdown report at `reports/<product>/<date>.md`.

The diff is computed in pure Python. The LLM is not asked to compute it.

### Synthesizer (`worker/src/product_search/synthesizer/`)

Takes today's filtered Listing rows + yesterday's diff + product-profile synthesis hints. Produces the markdown report.

The prompt is short and bounded:

```
Here is today's verified listing data as JSON, plus yesterday's diff and
profile hints. Produce a markdown report:

1. One-line bottom line: best path to the target, total cost, why.
2. Ranked table sorted by total_for_target_usd ascending. Include every
   row. Do not add columns. Do not omit rows.
3. Diff vs yesterday: new, dropped, price-changed >5%.
4. Flags in plain English.
5. ≤200 words of context. Profile hints, no padding.

Do NOT invent any field not present in the data. Do NOT modify any
number. If a field is null, write "unknown".
```

Because the LLM has no access to the web and the input is verified, fabrication is structurally impossible. The model just needs to follow instructions — which lets us use the cheapest tier. See [LLM_STRATEGY.md](LLM_STRATEGY.md).

### LLM provider abstraction (`worker/src/product_search/llm/`)

```python
def call_llm(
    *,
    provider: Literal["anthropic", "openai", "gemini", "glm"],
    model: str,
    system: str,
    messages: list[Message],
    response_format: Literal["text", "json"] = "text",
    max_tokens: int = 2048,
) -> LLMResponse:
    ...
```

One thin function. Provider-specific code lives behind it. This is what lets us benchmark, swap, and downshift to a cheaper model with a config change.

GLM uses the OpenAI-compatible endpoint at `https://open.bigmodel.cn/api/paas/v4/`. The OpenAI SDK with a base URL override is the simplest implementation.

### Web UI (`web/`)

Next.js App Router on Vercel. Three routes:

- `/` — list of products being tracked. Each card shows latest report's bottom line.
- `/[product]` — full latest report rendered, plus history of past reports, plus "Run now" button.
- `/onboard` — interview UI to add a new product. Produces a draft `profile.yaml`, shows it for review, commits to repo via the GitHub API.

Reports are read by fetching raw markdown from the GitHub repo (or, if private, via a small Vercel API route that uses a token). On-demand runs are triggered by a Vercel API route that calls `POST /repos/{owner}/{repo}/actions/workflows/search-on-demand.yml/dispatches`.

Mobile-first layout. Test at 375px viewport before claiming a UI task done.

### Scheduler (GitHub Actions)

- `search-scheduled.yml` — runs on a fan-out cron (every hour). On each run, it inspects each profile's `schedule.cron` and decides whether that product is due. This avoids one workflow file per product.
- `search-on-demand.yml` — `workflow_dispatch` with `inputs: { product: string }`. Web UI calls this.

Both workflows do the same work: install worker deps, run `python -m product_search.cli search <product>`, commit reports.

## Data flow for one daily run

1. Workflow starts, reads `products/<slug>/profile.yaml`.
2. For each `sources` entry in the profile, dispatch the named adapter with the listed query parameters. Adapters run concurrently (httpx async).
3. Concatenate all `Listing` results.
4. Run validator pipeline. Drop rejected, annotate flags, compute `total_for_target_usd`, compute QVL status.
5. Insert all rows into SQLite. Compute diff vs yesterday in Python.
6. Pass `(listings, diff, profile.synthesis_prompt_extras)` to synthesizer.
7. Write markdown report to `reports/<slug>/<date>.md`.
8. Workflow commits new report file.

## Generalization notes (RAM → anything else)

The seams that make this work for any product:

- **Profile YAML** declares spec attrs, filter rules, flag rules, sources, schedule, synthesis hints.
- **`Listing.attrs`** is a free-form dict whose schema the profile defines.
- **Validator rules** are data-driven (from profile), with the function library in code.
- **Adapters** take generic `AdapterQuery` shapes. RAM-specific or GPU-specific search strings live in profiles.
- **The reference data file** (`qvl.yaml` for RAM, `hcl.yaml` or whatever for other products) is referenced by the profile, not hardcoded.

When adding a second product type (say, GPUs), the work is: write the profile, add the GPU-specific source adapters if any are needed, add any new validator rules to the library that aren't already there. No rewrites.
