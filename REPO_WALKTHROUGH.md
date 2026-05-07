# Product Search — Repository Walkthrough

## 1. High-Level Purpose

**Product Search** is a scheduled, auditable price-and-availability tracker for hard-to-source products. It was built to solve a specific problem: conversational LLMs reliably fabricate prices, stock counts, and quotes when asked to "find listings." The architectural fix is structural — the LLM never claims a number it didn't read from a real fetched page.

The system works in stages:
1. **Deterministic scrapers** (per-source adapters) fetch real listings from vendor websites.
2. A **validator pipeline** rejects junk and flags risky listings.
3. An **LLM synthesizer** turns verified data into a ranked daily report (markdown).
4. A **mobile-friendly web UI** shows the latest report and lets you trigger a fresh run.

The system is **config-driven and product-agnostic**. It was designed around server RAM but can track any product type (GPUs, CPUs, headphones, etc.) by adding a new profile YAML.

---

## 2. Directory Structure

```
├── docs/                    # Design docs, decisions, session protocol
├── products/                # Product profiles (YAML configs per product)
│   ├── ddr5-rdimm-256gb/   # Example: DDR5 RAM profile
│   │   ├── profile.yaml    # What to buy, where to look, how to filter
│   │   └── qvl.yaml        # Qualified Vendor List (reference data)
│   └── _template/          # Template for new product profiles
├── reports/                 # Generated markdown reports (committed to git)
├── scratch/                 # Experimental code, not part of main pipeline
├── web/                     # Next.js frontend (deployed on Vercel)
├── worker/                  # Python backend — the core of the system
│   ├── src/product_search/  # Main package
│   │   ├── adapters/        # Per-source data fetchers
│   │   ├── validators/      # Filter, flag, and QVL logic
│   │   ├── storage/         # SQLite + CSV persistence
│   │   ├── synthesizer/     # LLM report generation
│   │   ├── llm/             # Provider abstraction (Anthropic, OpenAI, Gemini, GLM)
│   │   ├── models.py        # Shared data models (Listing, AdapterQuery)
│   │   ├── profile.py       # Profile YAML schema (Pydantic)
│   │   ├── config.py        # Runtime configuration
│   │   ├── cli.py           # CLI entry point
│   │   └── notify.py        # Push notification hooks
│   ├── tests/               # Unit tests
│   ├── benchmark/           # LLM model benchmarking suite
│   └── data/                # SQLite databases and LLM traces (gitignored)
└── .github/workflows/       # GitHub Actions (scheduler, on-demand dispatch)
```

---

## 3. Core Data Flow

```
Profile YAML → Adapters fetch → Validator pipeline → SQLite + CSV → Synthesizer (LLM) → Report MD
```

### 3.1. Product Profiles (`products/<slug>/profile.yaml`)

Each tracked product has a YAML profile that declares:

- **Target**: What you're trying to buy (e.g., "256 GB of DDR5 RDIMM, configured as 8×32 GB modules")
- **Spec attributes**: Product-type-specific fields (`capacity_gb`, `speed_mts`, `form_factor`, `ecc`, etc.)
- **Filter rules**: Hard rejection criteria (e.g., "reject if not ECC," "reject if speed < 4800")
- **Flag rules**: Warnings that keep the listing but annotate it (e.g., "flag if ships from China")
- **Sources**: Which adapters to query and with what search terms
- **QVL file**: A reference list of known-compatible part numbers
- **Schedule**: Cron expression for how often to run
- **Synthesis hints**: Qualitative guidance for the LLM report

The profile is validated by a Pydantic model in `worker/src/product_search/profile.py`.

### 3.2. Source Adapters (`worker/src/product_search/adapters/`)

Each adapter is a Python module that exports a `fetch(query: AdapterQuery) -> list[Listing]` function. They convert heterogeneous vendor pages into a single, homogeneous `Listing` dataclass.

**Available adapters:**

| Adapter | File | Source Type | Mechanism |
|---|---|---|---|
| `ebay_search` | `ebay.py` | eBay Browse API | OAuth2 + JSON API |
| `nemixram_storefront` | `nemixram.py` | Shopify storefront | JSON endpoint |
| `cloudstoragecorp_ebay` | `cloudstoragecorp.py` | eBay seller page | HTML parsing (selectolax) |
| `memstore_ebay` | `memstore.py` | eBay seller page | HTML parsing (selectolax) |
| `universal_ai_search` | `universal_ai.py` | Any vendor URL | JSON-LD extraction + LLM-assisted HTML parsing |

**Key design principles for adapters:**
- Never invent fields. If `quantity_available` isn't on the page, return `None`.
- Support **fixture mode**: during development, read from saved HTML/JSON files instead of hitting the network.
- Save raw responses to fixture files during dev runs (gated by env var).

### 3.3. The Listing Model (`worker/src/product_search/models.py`)

The `Listing` dataclass is the single shared shape every adapter produces. Key fields:

```python
@dataclass
class Listing:
    source: str              # "ebay_search", "nemixram_storefront", ...
    url: str                 # Direct product URL
    title: str
    fetched_at: datetime
    brand: str | None
    mpn: str | None
    attrs: dict[str, Any]    # Product-type-specific (capacity_gb, speed_mts, etc.)
    condition: str           # "new", "used", "refurbished"
    is_kit: bool
    kit_module_count: int
    unit_price_usd: float
    kit_price_usd: float | None
    quantity_available: int | None  # None = unknown, not 0
    seller_name: str
    seller_rating_pct: float | None
    seller_feedback_count: int | None
    ship_from_country: str | None
    qvl_status: str | None   # Set by validator
    flags: list[str]         # Set by validator
    total_for_target_usd: float | None  # Set by validator
```

The `AdapterQuery` dataclass carries search parameters from the profile to the adapter.

### 3.4. Validator Pipeline (`worker/src/product_search/validators/`)

A chain of pure functions that processes raw listings:

1. **AI Filter** (`ai_filter.py`): Uses an LLM to evaluate whether each listing is genuinely relevant to the product target. This replaced the earlier deterministic filter rules, as the LLM is better at semantic relevance (e.g., distinguishing "compatible with" from "is").

2. **Brand Inference** (`pipeline.py`): If the adapter left `brand` as `None`, tries to infer it from the title using word-boundary matching against `brand_candidates` from the profile.

3. **QVL Annotation** (`qvl.py`): Compares the listing's MPN/brand against the Qualified Vendor List to set `qvl_status` ("qvl", "inferred-compatible", "unknown", or "incompatible").

4. **Flag Application** (`flags.py`): Applies warning flags based on profile rules (e.g., low seller feedback, ships from China, Kingston "E" suffix).

5. **Total Cost Calculation** (`pipeline.py`): Computes `total_for_target_usd` — the cheapest way to fulfill the full target from this listing (e.g., if you need 8 modules and the listing sells kits of 4, it calculates 2 × kit_price).

### 3.5. Storage (`worker/src/product_search/storage/`)

- **SQLite** (`db.py`): Canonical store. One row per `(url, fetched_at)`. Supports history queries and diffing between snapshots.
- **CSV** (`csv_dump.py`): Per-run CSV dumps for inspection. Written to `reports/<slug>/data/`.
- **Diff** (`diff.py`): Pure-Python comparison between two snapshots. Reports new listings, dropped listings, and price changes ≥5%.

### 3.6. Synthesizer (`worker/src/product_search/synthesizer/`)

The synthesizer builds the daily markdown report. **Critical design decision:** the LLM contributes only one qualitative paragraph (the "Context" section). All other sections are built deterministically in Python:

- **Bottom line**: Cheapest passing listing, rendered from data.
- **Ranked listings table**: Sorted by `total_for_target_usd`, rendered from data.
- **Diff vs yesterday**: New/dropped/price-changed, rendered from the diff module.
- **Flags**: Deduplicated and sorted, rendered from data.
- **Context**: The LLM's only job — a ≤200-word qualitative summary.

**Post-check:** After the LLM generates the Context paragraph, a post-check scans it for any numeric digits not present in the input payload. If fabricated numbers are found, it retries once with a stricter prompt. If the retry also fails, the run is marked as failed and a stub report is written.

This architecture makes fabrication structurally impossible for the data sections and heavily constrained for the LLM section.

### 3.7. LLM Provider Abstraction (`worker/src/product_search/llm/`)

A single function `call_llm()` supports multiple providers:

```python
call_llm(
    provider="anthropic",  # or "openai", "gemini", "glm"
    model="claude-haiku-4-5",
    system="...",
    messages=[...],
)
```

Provider-specific code lives in separate modules (`_anthropic.py`, `_openai.py`, `_gemini.py`). GLM uses the OpenAI-compatible endpoint.

The **benchmark suite** (`worker/benchmark/`) was used to evaluate multiple models against the synthesis task. The winner was **GLM 4.5 Flash** (10/10 pass rate at $0/run), selected in ADR-012.

### 3.8. CLI (`worker/src/product_search/cli.py`)

The CLI is the main entry point, invoked by GitHub Actions:

| Command | Description |
|---|---|
| `validate <slug>` | Validate a product profile against the schema |
| `search <slug>` | Full pipeline: fetch → validate → store → synthesize report |
| `diff <slug>` | Show changes between the two most recent snapshots |
| `llm-ping <provider> <model>` | Test an LLM provider connection |
| `scheduler-tick` | Run search for all profiles whose cron matches the current hour |
| `probe-url <url>` | Diagnose a vendor URL through the universal_ai extraction pipeline |

The `search` command has flags: `--no-validate`, `--no-store`, `--no-report` for partial runs.

### 3.9. Web UI (`web/`)

A Next.js App Router application deployed on Vercel:

- **`/`** — List of products being tracked. Each card shows the latest report's bottom line.
- **`/[product]`** — Full latest report rendered, plus history of past reports, plus "Run now" button.
- **`/onboard`** — Interview UI to add a new product. Produces a draft `profile.yaml`, shows it for review, commits to the repo via the GitHub API.

**Key API routes:**
- `/api/dispatch` — Triggers an on-demand GitHub Actions run (authenticated via `WEB_SHARED_SECRET`).
- `/api/onboard/chat` — LLM-powered onboarding chat for creating new product profiles.
- `/api/onboard/save` — Commits the new profile to the repo via GitHub API.
- `/api/profile/[slug]` — CRUD for product profiles.
- `/api/push/subscribe` and `/api/push/notify` — Push notification support for material price changes.

Reports are read by fetching raw markdown from the GitHub repo. On-demand runs are triggered by dispatching a GitHub Actions workflow.

---

## 4. Running the System

### 4.1. Scheduled Runs (GitHub Actions)

- `search-scheduled.yml` — Runs every hour. Inspects each profile's `schedule.cron` and runs search for due products.
- `search-on-demand.yml` — Triggered manually or via the web UI's "Run now" button.

Both workflows: install worker deps → run `python -m product_search.cli search <slug>` → commit reports.

### 4.2. Local Development

```bash
cd worker
pip install -e ".[dev]"

# Fixture mode (no network calls)
WORKER_USE_FIXTURES=1 python -m product_search.cli search ddr5-rdimm-256gb

# Validate a profile
python -m product_search.cli validate ddr5-rdimm-256gb

# Diff between snapshots
python -m product_search.cli diff ddr5-rdimm-256gb

# Run tests
pytest
```

### 4.3. Environment Variables

| Variable | Purpose |
|---|---|
| `WORKER_USE_FIXTURES=1` | Use saved fixtures instead of live API calls |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | eBay Browse API credentials |
| `ALTERLAB_API_KEY` | Browser rendering service for JS-heavy sites |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | LLM provider keys |
| `WEB_SHARED_SECRET` | Shared secret for web → GitHub dispatch auth |

---

## 5. Adding a New Product

1. Copy `products/_template/` to `products/<new-slug>/`.
2. Edit `profile.yaml`: set target, spec_attrs, filters, flags, sources, schedule.
3. Create `qvl.yaml` with reference data (if applicable).
4. If new source adapters are needed, add them to `worker/src/product_search/adapters/`.
5. Register the new adapter ID in `profile.py`'s `KNOWN_SOURCE_IDS`.
6. Validate: `python -m product_search.cli validate <new-slug>`.
7. The next scheduled tick will pick it up automatically.

Alternatively, use the **web onboarding flow** at `/onboard`, which guides you through an LLM-assisted interview and commits the profile via the GitHub API.

---

## 6. Key Design Decisions

### Anti-Fabrication Architecture
The LLM never fetches, parses, or verifies data. It only synthesizes pre-verified JSON into a qualitative summary. All numbers in the report come from deterministic Python code, not the LLM.

### Config-Driven Product Types
The same code handles RAM, headphones, GPUs, etc. Product-specific logic lives in the profile YAML (spec_attrs, filter rules, search queries) and in adapters (which take generic `AdapterQuery` objects).

### Fixture-Based Testing
Every adapter has committed HTML/JSON fixtures. Tests run against fixtures, not live sites. When a site changes its HTML, the test fails — which is the correct behavior.

### Cheapest LLM for Synthesis
Since the LLM's job is narrowed to qualitative prose (not data extraction), the cheapest model that follows instructions is sufficient. The benchmark selected GLM 4.5 Flash at effectively $0/run.

### Git as the Source of Truth
Reports are markdown files committed to the repo. This gives free version history, diffing, and public readability. The web UI reads reports directly from GitHub.

---

## 7. Testing

Tests are in `worker/tests/` and cover:
- Profile schema validation (`test_profile.py`)
- Adapter parsing (`test_phase2.py`, `test_phase6.py`, `test_universal_ai.py`)
- Validator pipeline (`test_validators.py`)
- Storage and diffing (`test_storage.py`)
- Synthesizer and post-check (`test_synthesizer.py`)
- Benchmark criteria (`test_benchmark_criteria.py`)
- CLI commands (`test_cli.py`)
- AI filter responses (`test_ai_filter.py`)
- LLM wrapper edge cases (`test_openai_wrapper.py`)
- Pricing calculations (`test_pricing.py`)

Run with `pytest` from the `worker/` directory.

---

## 8. Current Status

Phases 0–5 are complete: scaffold, profile schema, eBay adapter (fixture mode), validator pipeline, SQLite + CSV storage, pure-Python diff, and the LLM synthesizer + multi-vendor benchmark.

Next up: Tier-A seller adapters (Phase 6) and additional vendor integrations. See `docs/PROGRESS.md` for live status.