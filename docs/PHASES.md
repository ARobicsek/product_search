# Phases

Each phase is sized to fit one focused dev session (~30-90 min with an AI co-pilot). Each section below is the brief a fresh session can read to start that phase. Briefs are intentionally short — the goal is "what to do" and "what done looks like," not "how to write Python."

## Phase 0 — Bootstrap repo

**Goal**: scaffold pushed to GitHub, CI green, decisions confirmed.

**Tasks**:
1. Confirm or override the `STATUS: PROPOSED` decisions in [DECISIONS.md](DECISIONS.md). Mark them `STATUS: ACCEPTED` once confirmed.
2. `git init`, set remote `origin` to `https://github.com/ARobicsek/product_search`, push the planning scaffold (this commit set).
3. Create `worker/` and `web/` empty package skeletons (just enough to make CI happy — `pyproject.toml` for Python, `package.json` for Next).
4. Create `.github/workflows/ci.yml` with lint + type-check + (no-op) test placeholders.
5. Set the four LLM API keys + eBay creds as GitHub Actions repository secrets.

**Done when**:
- Repo on GitHub shows the planning scaffold.
- CI runs green on `main`.
- Secrets are set (verified by a no-op echo step in CI that just checks they're present).

**Out of scope**: any actual scraping or LLM code. Adapter authoring waits until Phase 2.

---

## Phase 1 — Profile schema + DDR5 profile

**Goal**: declare the data model that every other phase consumes.

**Tasks**:
1. Define a Pydantic model in `worker/src/product_search/profile.py` that validates the schema sketched in `products/_template/profile.yaml`.
2. Write the concrete `products/ddr5-rdimm-256gb/profile.yaml`. Populate the QVL list at `products/ddr5-rdimm-256gb/qvl.yaml` from the part numbers listed in the handoff (Samsung M321R4GA0BB0-CQK, etc.).
3. Add `python -m product_search.cli validate <slug>` command. CI runs it on every commit touching `products/`.
4. Write tests: schema accepts the DDR5 profile, rejects malformed examples.

**Done when**:
- `cli validate ddr5-rdimm-256gb` passes.
- Schema rejects: missing required fields, unknown source IDs (against an allow-list set in code), invalid cron strings.
- CI green.

---

## Phase 2 — Worker skeleton + LLM abstraction + first adapter

**Goal**: end-to-end loop with one source. Stdout is fine — no storage, no synthesizer yet.

**Tasks**:
1. `worker/src/product_search/models.py` — the `Listing` dataclass per [ARCHITECTURE.md](ARCHITECTURE.md).
2. `worker/src/product_search/llm/__init__.py` + per-provider modules. Smoke-test each provider with `python -m product_search.cli llm-ping <provider> <model>`.
3. `worker/src/product_search/adapters/ebay.py` using the eBay Browse API. Search query comes from the profile's `sources` entry. Save raw response as a fixture under `worker/tests/fixtures/ebay/<descriptive>.json`.
4. `cli search ddr5-rdimm-256gb --no-validate --no-store` invokes the adapter with the profile's eBay search params and prints `Listing` rows as JSON to stdout.

**Done when**:
- Live `cli search` returns ≥5 real listings.
- Re-running offline with `WORKER_USE_FIXTURES=1` returns the same shape from saved JSON.
- All four LLM providers respond to `llm-ping` (just a "say hello" round-trip).

---

## Phase 3 — Validator pipeline

**Goal**: filtered, flagged listings only.

**Tasks**:
1. `worker/src/product_search/validators/filters.py` — the `reject_*` functions, driven by `profile.spec_filters`.
2. `worker/src/product_search/validators/flags.py` — the `flag_*` functions, driven by `profile.spec_flags`.
3. `worker/src/product_search/validators/qvl.py` — annotate `qvl_status` against `profile.qvl_file`.
4. `worker/src/product_search/validators/pipeline.py` — chain the rejecters and flaggers.
5. Tests against fixture listings: known-bad listings get rejected, known-flagged listings get flagged, QVL annotation is correct.

**Done when**:
- `cli search ddr5-rdimm-256gb` produces only listings that pass the filter chain, with flags annotated.
- Test coverage for the filters chain is meaningful (every filter has at least a positive and negative case).

---

## Phase 4 — Storage + diff vs yesterday

**Goal**: persisted history.

**Tasks**:
1. SQLite schema: `listings(url, fetched_at, ...all the Listing fields)`. Composite primary key on `(url, fetched_at)`.
2. `worker/src/product_search/storage/db.py` — insert and query.
3. Daily CSV dump to `worker/data/<slug>/<date>.csv` (gitignored).
4. `worker/src/product_search/storage/diff.py` — pure-Python diff between most recent two daily snapshots. New, dropped, price-changed >5%.
5. Tests: two synthetic snapshots in, expected diff out.

**Done when**:
- Two consecutive `cli search` invocations write rows to SQLite.
- `cli diff <slug>` prints the new/dropped/changed sets.

---

## Phase 5 — Synthesizer + multi-vendor benchmark

**Goal**: pick the cheapest passing model, then commit a daily report.

**Tasks**:
1. `worker/src/product_search/synthesizer/prompts/synth_v1.txt` — the prompt from [ARCHITECTURE.md](ARCHITECTURE.md).
2. `worker/src/product_search/synthesizer/synthesizer.py` — render the prompt with today's listings JSON + diff + profile hints; call the configured LLM; post-check the output (no fabricated numbers/URLs).
3. `worker/benchmark/` — fixtures (≥10 listing-set JSONs), test bar checks per [LLM_STRATEGY.md](LLM_STRATEGY.md), pricing table, runner.
4. Run the benchmark across all four providers' cheap-tier models. Pick the cheapest passing model. Record the choice in [DECISIONS.md](DECISIONS.md) with cost data.
5. Wire the chosen model into the synthesizer via `LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL` env vars.
6. `cli search <slug>` now also writes `reports/<slug>/<date>.md`.

**Done when**:
- A benchmark report is committed at `worker/benchmark/results/<date>.md`.
- A daily report is committed for `ddr5-rdimm-256gb`.
- Post-check fails the run if the report contains a price/URL/MPN not present in the input.

---

## Phase 6 — Tier A adapters

**Goal**: real source diversity. Get to 20+ listings per run.

**Tasks**:
1. `adapters/nemixram.py` — Shopify `/products/<handle>.json` endpoints. The user has flagged NEMIX as a known-good seller; the storefront URL is in the profile.
2. `adapters/cloudstoragecorp.py` — eBay seller-store scrape. Save fixture HTML.
3. `adapters/memstore.py` — eBay seller-store scrape, with the generic-MB-modules flag explicitly applied.
4. Tests against fixtures for each.

**Done when**:
- A live run produces listings from all four sources (eBay search + 3 sellers).
- Offline tests pass against fixtures.

---

## Phase 7 — Scheduling

**Goal**: scheduled and on-demand runs working in production.

**Tasks**:
1. `cli scheduler-tick` — walk profiles, run those whose cron matches the current UTC hour.
2. `.github/workflows/search-scheduled.yml` — hourly cron, calls `scheduler-tick`, commits reports.
3. `.github/workflows/search-on-demand.yml` — `workflow_dispatch` with `product` input.
4. Test: trigger on-demand run from `gh workflow run`. Verify a report is committed.

**Done when**:
- Scheduled workflow has run at least once on its own and committed a report.
- `gh workflow run search-on-demand --field product=ddr5-rdimm-256gb` produces a fresh report.

---

## Phase 8 — Web UI MVP (installable PWA shell)

**Goal**: the daily report is viewable from a phone, and the site is installable to the iOS Home Screen.

**Tasks**:
1. `web/` Next.js App Router project. Tailwind. Mobile-first.
2. PWA fundamentals from the start:
   - `web/public/manifest.webmanifest` with `display: standalone`, theme color, name, short_name, and a clean icon set (192/512 PNG plus maskable).
   - A minimal service worker (next-pwa or hand-rolled) with a network-first cache for HTML and stale-while-revalidate for static assets.
   - "Add to Home Screen" affordance on iOS Safari (a small banner that detects iOS standalone mode and disappears once installed).
3. `/` route — list of products, each card with latest report's bottom line + last-run timestamp.
4. `/[product]` route — full report rendered (markdown), plus history list (links to past reports).
5. Reports are read by fetching raw GitHub URLs.
6. Deploy to Vercel under the user's team (https://vercel.com/aris-projects-b1e40d05). Production URL set up.
7. Eyes-on test at 375px viewport. Verify "Add to Home Screen" works on a real iPhone.

**Done when**:
- Vercel deploy URL renders the latest DDR5 report on a phone, readable without horizontal scrolling outside of tables.
- The site can be installed to iOS Home Screen and opens in standalone mode (no Safari chrome).
- History list shows committed reports.

---

## Phase 9 — On-demand trigger from web

**Goal**: "Run now" button works end-to-end.

**Tasks**:
1. `/api/dispatch` route in the web app. Calls GitHub `workflow_dispatch`. Auth via `WEB_SHARED_SECRET` header.
2. UI: "Run now" button on `/[product]`. State machine: idle → dispatching → running → done (with new-report-timestamp diff).
3. Polling: client polls GitHub run status until `completed`, then calls `revalidatePath('/[product]')`.
4. Test the loop end-to-end on the deployed Vercel.

**Done when**:
- Clicking "Run now" on the deployed site triggers a real GH Actions run and shows the new report when complete.
- The button is disabled while a run is in flight.

---

## Phase 10 — Onboarding interview

**Goal**: add a new product type without touching files.

**Tasks**:
1. `worker/src/product_search/onboarding/prompts/onboard_v1.txt` — system prompt per [PRODUCT_ONBOARDING.md](PRODUCT_ONBOARDING.md).
2. `web/api/onboard/chat` — proxy to the configured onboarding LLM, streaming.
3. `/onboard` route — chat UI with a transcript pane and a "draft profile" pane that fills in as the conversation progresses.
4. Add a web-search tool to the onboarding LLM so it can suggest potential Tier B/C sources for long-tail products.
5. `web/api/onboard/save` — validate proposed YAML against the profile schema (call out to the worker's validator), then commit `products/<slug>/profile.yaml` via the GitHub Contents API.
5. End-to-end test: onboard a fake "test-product-foo" type, verify it commits and validates.

**Done when**:
- A user can complete an onboarding from a phone and see the new product appear on `/`.
- The committed profile validates in CI on its own commit.

---

## Phase 11 — iOS push notifications for alerts

**Goal**: when something material changes (new entrant, ≥5% price drop, new cheapest path to target), the user's iPhone gets a push notification.

**Prerequisite**: PWA shell from Phase 8 is live and installable. iOS push only works when installed to Home Screen.

**Tasks**:
1. Generate VAPID key pair. Store as Vercel env vars: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (a `mailto:` URL).
2. Subscription flow in the web app:
   - On `/[product]`, an "Enable alerts" button (visible only when running as an installed PWA — detect `display-mode: standalone`).
   - On click: `Notification.requestPermission()`, then `serviceWorker.pushManager.subscribe({ applicationServerKey: VAPID_PUBLIC_KEY })`.
   - POST the subscription JSON to `/api/push/subscribe`. Store in **Vercel KV** keyed by a stable client ID.
3. Service worker `push` handler — render a notification with title, body, and a deep link back to `/[product]`.
4. `/api/push/notify` route — accepts a push payload, fans it out to every stored subscription using `web-push`. Auth via `WEB_SHARED_SECRET` header (same secret as `/api/dispatch`).
5. Worker side: after a scheduled or on-demand run, the CLI computes whether the diff is "material" (rules in code, defaults: any new cheapest path, any ≥5% price drop, any new listing). If material, POST to `/api/push/notify` with `{ product, headline, url }`.
6. End-to-end test: install the PWA on a real iPhone, enable alerts, trigger an on-demand run that produces a material diff, verify the notification arrives.

**Done when**:
- An installed iOS PWA receives a push when the worker reports a material change.
- "Enable alerts" can be revoked from the same UI.
- Pushes contain a working deep link back to the relevant product report.

---

## Phase 12 — Polish & second product proof

**Goal**: prove the generality. Add a non-RAM product type end-to-end.

**Tasks** (loosely):
- Pick a second product (suggestion: GPUs for AI inference, or PSUs ≥1600W Platinum).
- Use the onboarding flow to add the profile.
- Add any missing source adapters identified during onboarding.
- Run scheduled. Verify the daily report works.
- Optional: price-drop alerts (Slack webhook), price-history sparklines on the web UI.

**Done when**:
- Two products run scheduled in production, with reports committed daily, and the system worked the same for both.
