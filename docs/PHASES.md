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

---

## Phase 13 — Verify & stabilize the AlterLab vendor-render path

**Goal**: confirm the ScrapFly → AlterLab swap actually works in prod, and lock it in with an ADR. The migration code is already in [worker/src/product_search/adapters/universal_ai.py](worker/src/product_search/adapters/universal_ai.py) and both workflow files but is uncommitted at session start; `ALTERLAB_API_KEY` is confirmed set in GH Actions secrets.

**Prerequisite**: `ALTERLAB_API_KEY` set in repo secrets (confirmed 2026-05-02).

**Tasks**:
1. Commit the pending migration changes (universal_ai.py, both workflow ymls, test_universal_ai.py, cli.py, .env.example).
2. Trigger a Run-now on `bose-nc-700-headphones`. Pull the worker stderr from the GH Actions log; verify each `universal_ai_search` source emits `[universal_ai] Fetched via alterlab`. If any fell through to curl_cffi, the API key isn't reaching the worker.
3. For each of the four Bose vendors (backmarket, bhphotovideo, bestbuy, gazelle), classify the result:
   - ≥1 listing emitted → success.
   - 0 listings + non-empty HTML body → candidate-extraction problem (defer to Phase 15).
   - 0 listings + empty / challenge body → AlterLab itself failed; capture the response body to a fixture for diagnosis.
4. Write ADR-033 documenting the ScrapFly → AlterLab swap: motivation (credit exhaustion), reliability comparison, cost comparison, fallback semantics.
5. If any AlterLab-specific error path needs handling (auth/quota errors at runtime), make sure they bubble up to the UI cleanly via cli.py's existing error path — already wired but verify on a real failure.

**Done when**:
- One green Run-now on Bose with `Fetched via alterlab` in the worker log for every universal_ai source.
- ADR-033 in DECISIONS.md.
- A clear written verdict per vendor in PROGRESS.md ("backmarket: 3 listings, ok; gazelle: 0/0, extraction issue, deferred to Phase 15"; etc.).

**Out of scope**: improving the extractor itself (Phase 15), changing the onboarder (Phase 14).

---

## Phase 14 — Onboarder cost & memory rebuild

**Goal**: cut average onboarding session cost by ≥70% AND make "what product are we talking about?" failures structurally impossible.

**Decisions locked in by user (2026-05-02)**:
- Switch onboarder model from `glm-5.1` → `claude-haiku-4-5`. Use Anthropic's native `web_search` tool. Use prompt caching on the system prompt.
- Keep YAML as the on-disk profile format. Switch the per-turn assistant output from full YAML → structured intent JSON; render YAML server-side at save time only.

**Tasks**:
1. **Re-platform [web/app/api/onboard/chat/route.ts](web/app/api/onboard/chat/route.ts) to Anthropic SDK + Claude Haiku 4.5.** Default `LLM_ONBOARD_PROVIDER=anthropic`, `LLM_ONBOARD_MODEL=claude-haiku-4-5`. Wire Anthropic's `web_search` tool (multi-turn — server handles tool_use → tool_result roundtrips and streams the post-search assistant text). Update `.env.example`.
2. **Enable Anthropic prompt caching** on the system prompt + the static-schema portion of `messages[0]`. Cache breakpoint at the seam between "static schema docs" and "conversation". Cuts repeat-turn cost ~90%.
3. **Decisions ledger pattern.** Update [worker/src/product_search/onboarding/prompts/onboard_v1.txt](worker/src/product_search/onboarding/prompts/onboard_v1.txt) to instruct the assistant to emit a `<state>{...json...}</state>` block at the end of every assistant message, containing the running list of confirmed decisions (slug, target, filters, flags, sources, columns, schedule). Server-side: when trimming the sliding window, replace the dropped middle turns with one synthetic `assistant` turn that contains only the most recent `<state>` block. Result: the model always sees `messages[0]` (kickoff) + latest decisions ledger + last 4 conversational turns.
4. **Structured intent JSON instead of YAML in turns.** Replace per-turn YAML emission with a `<draft>{...json...}</draft>` block matching the same schema. Server-side `web/lib/onboard/render-yaml.ts` deterministically renders YAML from the JSON at save time. Eliminates the "model dropped a closing brace" failure class and shrinks output tokens.
5. **Update [web/app/onboard/OnboardChat.tsx](web/app/onboard/OnboardChat.tsx)** to parse the new `<state>` and `<draft>` block format; the right-pane preview shows the rendered YAML (regenerated client-side per turn for the user, server-side at save time as the source of truth).
6. **Bench on one real onboarding session.** Onboard a hypothetical product (e.g. "noise-cancelling headphones budget under $300") through ~15 turns including web search. Compare against the GLM-5.1 baseline: input/output tokens, $ cost, and whether the model ever loses the slug or display_name.

**Done when**:
- A 15-turn session about a multi-spec product ends with a valid profile and the model never loses the slug, display_name, or any decision confirmed in turn ≤4.
- Average cost per session ≤30% of the GLM-5.1 baseline (measured on one real session).
- Web search still works (verifiable: ask a vendor-discovery question that requires it).

**Out of scope**: changing the YAML schema itself, changing the universal adapter (Phase 15).

---

## Phase 15 — Universal adapter quality pass

**Goal**: take the universal adapter from "works on backmarket only" to "works on most major e-commerce stacks." Stop adding vendor URLs that score 0/0.

**Tasks**:
1. **Add JSON-LD / microdata extraction tier** to [worker/src/product_search/adapters/universal_ai.py](worker/src/product_search/adapters/universal_ai.py). Walk all `<script type="application/ld+json">` blocks; if any contain `Product` / `Offer` / `ItemList` types, extract `name`, `offers.price`, `url` directly. Most modern e-commerce embeds this for SEO. Tried BEFORE the anchor heuristics; falls through if no JSON-LD found. Zero LLM cost when it works.
2. **Add `cli probe-url <url> [--render]` command** to [worker/src/product_search/cli.py](worker/src/product_search/cli.py). Fetches via the same tier chain as the adapter (with optional AlterLab forced via `--render`), runs candidate extraction, prints: fetcher used, status, body length, candidate count, JSON-LD count, and 3 sample candidates with title + price. Returns nonzero exit if candidate count is 0. Useful for both manual diagnosis and the onboarder hook in step 4.
3. **Capture fixtures from 6 real vendors** representing different stacks: Shopify (e.g. headphones.com), Magento, BigCommerce, custom React (e.g. bestbuy), refurb marketplace (backmarket), big-box (bhphotovideo or newegg). Fixtures live in `worker/tests/fixtures/universal_ai/<vendor>.html`. Pin extractor behavior in tests — both JSON-LD path AND anchor heuristics.
4. **Tighten anchor heuristics** based on fixture failures: loosen `_PRICE_PATTERN` to handle split-price markup (e.g. `<span class="price">249</span><sup>99</sup>`); add support for sites where prices live in `data-price` attributes; consider raising `max_candidates` or paginating.
5. **Onboarder integration** (depends on Phase 14): when the AI proposes a `universal_ai_search` URL, it MUST first call the probe (via a new `probe_url` tool exposed to the assistant). URLs scoring 0 land in `sources_pending` with an explicit "probe returned 0 candidates" note instead of `sources`. The user never gets a profile that silently has dead vendor URLs.

**Done when**:
- 4 of 6 fixture vendors yield ≥3 listings each via the offline test.
- `cli probe-url` works locally and from CI.
- A new onboarding session can't add a 0/0 URL to `sources` — it routes to `sources_pending`.
- ADR-034 documenting the JSON-LD tier + probe pattern.

**Out of scope**: writing native per-vendor adapters (those are Tier-A work, separate scope).

---

## Phase 16 — Slug deletion (hard delete)

**Goal**: a delete button on the home page that actually removes a product end-to-end. Hard delete confirmed by user (2026-05-02).

**Tasks**:
1. **`DELETE /api/profile/[slug]` route.** Auth via `WEB_SHARED_SECRET` header (same pattern as `/api/dispatch`). Deletes via the GitHub Contents API:
   - `products/<slug>/profile.yaml`
   - `products/<slug>/qvl.yaml` (if present)
   - Every file under `reports/<slug>/` (markdown reports + per-run CSVs under `data/`)
   - Issues a single commit: `chore: delete product <slug>`.
2. **Home-page UI**: small delete button per product card. Opens a confirmation modal with destructive-styled red Delete button (no typed-slug confirmation — see round-3 paper-cuts cleanup, 2026-05-10). On success, `revalidatePath('/')` and the card disappears.
3. **Edge cases**: deletion mid-Run-now must not break — the in-flight workflow run will commit a report into a now-empty directory; that's tolerable (orphaned report, no profile to read it). Document this in the ADR.
4. **Tests**: a unit test for the delete handler that mocks the Contents API and asserts the right files are deleted; manual test that deleting the bose profile actually removes the directory tree.

**Done when**:
- Deleting `bose-nc-700-headphones` (or any test slug) removes `products/<slug>/` AND `reports/<slug>/` from the repo via a single auto-commit.
- The home page no longer lists the deleted product.
- ADR-036 in DECISIONS.md (auth model, what gets deleted, mid-run safety). (ADR-035 was claimed during the Phase 15 prelude for the Run-now UX wipe + Actions API consistency fix.)

---

## Phase 17 — Schedule editor + alerts UI

**Goal**: change a product's schedule from the web UI, and let the user configure price/vendor alerts that fire push notifications to the PWA. No YAML hand-editing. (Was Phase 12c in the old plan; scope expanded 2026-05-11 to include alerts.)

**Scope notes**:
- Alerts are configured in the editor UI **only**. The LLM onboarder must NOT ask about alerts during onboarding — it's purely user-driven post-onboarding config.
- The PWA push pipeline already exists end-to-end: [SubscribeButton.tsx](../web/app/[product]/SubscribeButton.tsx) opts the user in (PWA standalone only); [api/push/subscribe](../web/app/api/push/subscribe/route.ts) stores VAPID subscriptions in Upstash Redis; [api/push/notify](../web/app/api/push/notify/route.ts) fans out a `{ product, headline, url }` payload; [public/sw.js](../web/public/sw.js) handles the push event. We're wiring alerts into this existing pipeline, not building push from scratch.

**Tasks**:

### Part A — Schedule editor
1. **Inline schedule editor** on `/[product]`. Common-case picker (radio): daily 08:00 UTC / hourly / every 6h / every 12h / custom cron. Picker writes to `profile.yaml.schedule.cron`. Must also support **clearing** the schedule (no scheduled runs; run-now only) — writes `schedule: null` or removes the key (per round-2 schema change in [profile.py](../worker/src/product_search/profile.py)).
2. **Local-time display**: show "next scheduled run: <user's local time>" computed from the cron string + current UTC. Use the same client-component pattern as RunInfoFooter.
3. **Save flow**: extend the surgical-mutator pattern in [web/lib/report-columns.ts](../web/lib/report-columns.ts) with `applyScheduleToYaml(yamlText, scheduleOrNull)` and `readScheduleFromYaml(yamlText)`. Reuse the `/api/profile/[slug]` PUT path (analogous to the existing DELETE in [api/profile/[slug]/route.ts](../web/app/api/profile/[slug]/route.ts)), or add a sibling route. Whichever path lands files via the same `commit.ts` plumbing.
4. **Client-side cron validation** — reject invalid 5-field crons before save.

### Part B — Alert rules schema
5. **Add `alerts:` block to `Profile`** in [worker/src/product_search/profile.py](../worker/src/product_search/profile.py) and mirror in [web/lib/onboard/schema.ts](../web/lib/onboard/schema.ts). Default `[]`. Onboarder prompt is NOT updated — the LLM does not propose alerts. Two rule kinds:
   - `{ kind: "price_below", threshold_usd: float, condition?: "new"|"used"|"refurbished" }` — fires when the cheapest passing listing's `price_unit` is below threshold (filtered by condition if set).
   - `{ kind: "vendor_seen", host: str }` — fires when ≥1 passing listing has its vendor host matching `host` (canonical match per ADR-020).

### Part C — Worker-side evaluator
6. **After `synth` produces the ranked listings**, evaluate each alert rule against the run's listings + the previous run's listings (loaded from the prior day's `.csv` under `reports/<slug>/data/`). Fire on **transitions**, not on every matching run:
   - `price_below` fires when the current-run cheapest crosses *below* threshold and the previous-run cheapest was at or above (or there was no previous run).
   - `vendor_seen` fires when the current run has ≥1 passing listing for `host` and the previous run had 0 (or no previous run).
   This avoids notification fatigue and matches the user-intuitive "something happened" framing.
7. **Fire notifications** by POSTing to `/api/push/notify` with a Bearer token (`PUSH_NOTIFY_SECRET`). Payload: `{ product: <slug>, headline: <human-readable summary>, url: /<slug> }`. One POST per fired rule. Log fired-rule audit trail to the run output (existing run-cost panel pattern).

### Part D — Alerts UI
8. **"Alerts" section in the editor** (sibling to the schedule picker). Lists current rules with edit/delete buttons; "+ Add alert" opens a small inline form. Surgical-mutator: `applyAlertsToYaml(yamlText, rules)` / `readAlertsFromYaml(yamlText)` in a new `web/lib/alerts.ts`.
9. **Subscribe-state nudge**: if the user adds an alert but has not opted in to push (no Upstash subscription for this PWA), show an inline prompt: "Tap 'Enable Alerts' above to receive push notifications." Don't auto-trigger the subscribe flow — the user opts in explicitly.

### Part E — Verification
10. **Test schedule editor**: change a product's schedule from daily 08:00 → every 6h, verify the next `scheduler-tick` workflow invocation picks up the new cron. Also test clearing the schedule.
11. **Test alerts**: add a `price_below` rule with a threshold above the current cheapest (should NOT fire — already below); set threshold above and have the next run pass through it (should fire). Add a `vendor_seen` rule for a vendor that didn't return listings last run; trigger a run-now and verify the push fires once. Verify no re-fire on the next run when the condition persists.

**Done when**:
- Editing a product's schedule from the UI is reflected in the next scheduled-run workflow tick.
- Clearing the schedule from the UI causes the scheduler to skip that profile.
- The user can add/remove price-threshold and vendor-seen alert rules from the UI; rules persist to `profile.yaml`.
- A scheduled run that triggers an alert fires exactly one push notification per rule per transition (no spam across consecutive same-state runs).
- Cron strings validated client-side; alert threshold/host strings validated client-side.
- `tsc --noEmit` clean; worker tests stay green with new alerts-evaluator tests added.

**Out of scope**:
- Onboarder-time alert suggestions (explicitly NOT wanted).
- Notification grouping/digest (single push per fired rule is fine for v1).
- Email / SMS fallback (PWA push only; matches existing infra).
- Per-user alert preferences (alerts are per-product, not per-subscriber — every subscribed device gets every fired alert).

---

## Phase 18 — Polish & second product proof (replaces old Phase 12)

**Goal**: end-to-end proof that the rebuilt system works for a third product type.

**Tasks**:
- Onboard one new non-RAM, non-headphones product end-to-end using the rebuilt onboarder (Phase 14) + improved adapter (Phase 15). Suggestion: a category that exercises web search hard (e.g. mechanical keyboards or a specific GPU model).
- Use the schedule editor (Phase 17) to set a non-default cadence.
- Run scheduled for 7 consecutive days. Verify reports land daily.
- At end of week, delete one of the three products via the delete button (Phase 16) to validate that path on real data.

**Done when**:
- Three products onboarded, one deleted, two run scheduled for a full week with daily reports committed.

---

## Phase 19 — Universal adapter accuracy & vendor reach (urgent)

**Goal**: stop emitting wrong prices, and decide what to do with universal_ai sources that bot-block. The first two live runs through the Phase-15 pipeline (Bose + Breville, 2026-05-04) showed Phase 15 fixed coverage but introduced an accuracy problem and didn't fix the vendor-reach problem at all.

**Why this jumps ahead of Phases 16–18**: Phase 18 (polish + second-product proof) requires runs to produce reliable data for a week. Today's universal_ai output is unreliable (wrong Amazon prices) and incomplete (most vendors return 0). Both undermine the value proposition of universal_ai_search and cost real LLM tokens for nothing. Slug deletion and schedule editor are features; this is correctness, and correctness comes first.

**Concrete observations driving the brief** (full data in PROGRESS.md "Run 1" / "Run 2" sections):
- Breville Amazon: 3 listings shipped, all 3 with prices materially different from the live page. User-reported example: BES876BSS recorded as $489.50; live page is $649.95. Likely root cause: 1500-char ancestor walk pulls in "From: $X" / used-condition prices alongside the new price; LLM picks the cheapest.
- Bose run, 7 universal_ai sources: 0 fetched / 0 passed for amazon, backmarket, bestbuy, walmart, crutchfield, reebelo. Bose.com /c/refurbished: 17 fetched / 0 passed because Bose discontinued the NC 700 — that URL will never yield NC 700 listings.
- Run cost on the 0-yield Bose run: $0.016 spent on universal_ai LLM calls that produced nothing usable.

**Tasks**:

1. **Diagnose Amazon price attribution.** Read the breville-barista-express CSV and compare each Amazon `unit_price_usd` against the live page. Quantify: 1 of 3 wrong, 3 of 3 wrong, etc. Then either (a) tighten `_ancestor_card_text` to stop at smaller per-card containers (heuristic: stop when ancestor text length first exceeds 800 chars OR when ancestor's tag is `<li>`/`<article>` AND the parent has multiple siblings of the same tag — i.e. we've reached the card list), or (b) add an Amazon-specific selector that prefers the first `<span class="a-offscreen">` inside the same `s-result-item`/`a-card-container` as the title anchor. Pin against a real Amazon search-result fixture.

2. **Per-vendor body-fixture capture for the 0-yield sources** in the bose profile. Run `cli probe-url` on each of {amazon, backmarket, bestbuy, walmart, crutchfield, reebelo} URL and save the `--render` body to `worker/tests/fixtures/universal_ai/<vendor>-bose-2026-05-04.html`. For each: classify "AlterLab can't bypass the bot tier" vs "AlterLab gets through but the page doesn't carry NC 700" vs "extractor genuinely missed candidates." Document one-line verdict per vendor.

3. **Remove or replace dead URLs in the bose profile.** `bose.com/c/refurbished` is the prime candidate — Bose discontinued the NC 700, so the URL will never carry the target product. Decide whether the onboarder should be smarter at vendor-discovery time (only suggest vendors that currently sell the product, not just match the brand) or whether the user manually curates after the first run. Document the decision.

4. **Vendor-reach policy.** For URLs that AlterLab can't bypass, decide systemically: keep them in `sources` (run them every day, accept 0 yield, hope rendering improves) vs auto-demote to `sources_pending` after N consecutive 0-yield runs. The latter would be a small change to the worker pipeline (track per-source 0-yield streaks in the SQLite store; surface in the next-run cli output).

5. **Re-run Bose + Breville and verify**:
   - Breville BES876BSS records the correct $649.95 (or matches Target's page price).
   - Bose run cost drops to ≤$0.005 (no token waste on bose.com /c/refurbished).
   - Sources panel shows ≥1 universal_ai vendor producing real listings (target on Breville, ideally backmarket on Bose if AlterLab cooperates that day).

6. **Tier 1.5 — detail-page price extractor** (scoped 2026-05-17; see ADR-049). Motivation: for single-SKU products (one exact part number, e.g. AMD EPYC 9255 / `100-000000694`), the vendors that actually stock the item (SabrePC, Wiredzone, ServerSupply, IT Creations, Central Computer, Newegg) expose it ONLY on JS-heavy product **detail** pages with **no JSON-LD** and **no clean product anchors** — just nav junk. Tier 1 (JSON-LD) misses; Tier 2 (anchor→LLM) correctly rejects the junk and emits 0. Empirically (rendered `probe-url`, 2026-05-17) every non-eBay vendor for the 9255 yields 0; eBay is the only extractable source, which makes "remove eBay" impossible for this product class until this tier exists.

   **Design**:
   - **New tier between JSON-LD and anchor extraction** in `universal_ai.fetch()`. Runs ONLY when (a) Tier 1 returned nothing AND (b) the source is flagged as a single-product detail page.
   - **Explicit profile opt-in (preferred over heuristic).** Add optional `page_type: "detail" | "search"` to the `Source` model in `worker/src/product_search/profile.py`, mirror it in `web/lib/onboard/schema.ts` (the recurring Pydantic/TS sync rule), and surface it in `KNOWN_*` if needed. `_looks_like_product_url()` (already at universal_ai.py:664) is the fallback classifier when `page_type` is absent. Deterministic opt-in avoids mis-firing detail extraction on real search/category pages where Tier 2 is correct.
   - **Extraction**: strip HTML to main content (drop `<script>/<style>/<nav>/<header>/<footer>`, collapse whitespace, cap ~12–16k chars to bound tokens). One `claude-haiku-4-5` call with a new `DETAIL_SYSTEM_PROMPT`: "This page is ONE product. Return `{found, title, price_usd, condition, in_stock, pack_size}`. The price MUST appear verbatim in the provided text; if absent, `found:false`. Do NOT invent."
   - **Architecture-rule guard (hard requirement, ADR-001)**: after the LLM returns, deterministically verify the extracted `price_usd` occurs in the fetched/stripped text under normalization (strip `$`, `,`, whitespace; tolerate `2,335.00`/`2335`/`$2,335`). Drop the listing if not found. URL is ALWAYS the source URL — never LLM-produced. This makes Tier 1.5 stricter than Tier 2.
   - **Emission**: single `Listing`, `attrs.extractor = "detail_llm"`, `seller_name = host`, condition/pack via existing `_parse_pack`, `in_stock` carried so the `in_stock` filter works.
   - **probe-url**: extend `_cmd_probe_url` to run Tier 1.5 for detail-typed sources (or a `--detail` flag) so the onboarder gate reflects the new capability.
   - **Onboarder prompt** (`onboard_v1.txt`): for single-SKU products (exact MPN/part number), a vendor product-detail URL with `page_type: detail` is now a VALID `sources` entry — NOT auto-`sources_pending`. Narrow this exception to single-SKU domains; multi-listing products keep the existing search-URL-preferred guidance.
   - **Testing (committed fixtures, per SESSION_PROTOCOL)**: `probe-url --save-body --render` the SabrePC + Wiredzone + IT Creations + ServerSupply + CentralComputer detail pages into `worker/tests/fixtures/universal_ai/<vendor>-epyc9255-2026-05-17.html`. Unit tests: correct priced `Listing` from SabrePC/Wiredzone fixtures; `found:false` when no price; the price-verbatim guard rejects a fabricated price; gating (detail vs search URL) routes to the right tier.
   - **Known risks / open questions**: ServerSupply & CentralComputer returned ~32KB rendered bodies with 0 extractable content — possibly Datadome walls AlterLab only partially defeats; scope expectation = SabrePC + Wiredzone + IT Creations are the realistic wins, ServerSupply/CentralComputer best-effort (inspect saved bodies first). AlterLab intermittently 422s Newegg/IT Creations detail URLs — a fetch-reliability issue (consider one retry/backoff on 422) orthogonal to extraction. Cost: replaces today's wasted Tier-2 junk call with one bounded detail call; net neutral-to-cheaper.
   - **Then**: re-probe the parked URLs in `products/amd-epyc-9255/profile.yaml`, promote the ones Tier 1.5 extracts back into `sources`, and remove `ebay_search` (the originally-requested change, unblocked once ≥1 non-eBay source works).

**Done when**:
- Amazon price recorded for at least one popular product matches the live new-condition price within $5 (tolerance for tax/discount drift).
- Bose profile is free of guaranteed-zero URLs (`bose.com /c/refurbished` removed or replaced).
- Per-vendor verdict document committed to docs/ explaining each 0-yield vendor's status.
- ADR-039 documenting the price-attribution fix and the vendor-reach policy.
- Tier 1.5 extracts a correct priced listing from ≥2 of the parked amd-epyc-9255 detail URLs; `amd-epyc-9255` runs eBay-free with ≥1 working non-eBay source; ADR-049 marked implemented.

**Out of scope**: writing native per-vendor adapters (still Tier-A, separate work). Replacing AlterLab with another fetch tier (separate evaluation if Phase 19 conclusions warrant it).

---

## Phase 20 — Reliable scheduling trigger (external `workflow_dispatch`)

**Goal**: schedules (one-time `run_at` and recurring crons) fire within ~15 min of their time, reliably — instead of GitHub's observed ~hourly collapse.

**Context (why this phase exists)**: GitHub Actions `schedule:` is best-effort and deprioritized under load. Empirically on 2026-05-17 the `*/15` heartbeat ran at intervals of [64, 57, 62, 63] min — effectively hourly. Net effect: a user's one-time "run at 2:49 PM" job landed ~55 min late (this bit the user three sessions running). The scheduler/profile/one-time logic is **correct** (`run_at <= now`, not windowed — verified); the only defect is GitHub's trigger cadence. The fix (industry-standard) is to keep the job logic in Actions but trigger it from a reliable external scheduler via the un-throttled `workflow_dispatch` API. The user is on **Vercel Hobby** and requires a **free** solution, so the chosen design routes the external trigger through the *existing* Vercel app so the powerful GitHub PAT never leaves infra we control. Full rationale + rejected alternatives: **ADR-052**.

**Chosen architecture (the "hybrid", per ADR-052)**:

```
cron-job.org (free, every 15 min)
  → POST https://<vercel-app-domain>/api/cron/tick   (header: x-cron-secret: <CRON_TRIGGER_SECRET>)
    → web route validates secret, calls dispatchScheduledTick()
      → POST GitHub API /actions/workflows/search-scheduled.yml/dispatches {ref:"main"}
        (uses GITHUB_DISPATCH_TOKEN already in Vercel env — NOT stored at cron-job.org)
          → scheduler-tick runs in GitHub Actions exactly as today
```

Why this shape: cron-job.org only ever holds a low-value shared secret + a URL. If that leaks, the worst an attacker gets is "force a scheduler tick" (cost: a search run only if a profile is actually due; most are scheduleless). The GitHub PAT — which can burn paid LLM/scrape budget and push to `main` — stays in Vercel env where it already safely lives and is already used by `dispatchOnDemandRun`. Free on Hobby because a normal inbound API route is *not* subject to Vercel's daily-cron frequency cap (that cap is only for Vercel Cron jobs).

**Tasks**:
1. **`web/lib/dispatch.ts`** — add `dispatchScheduledTick()`: `POST` `workflow_dispatch` to `SCHEDULED_WORKFLOW_FILE` (`search-scheduled.yml`, constant already added in ADR-051 work), `ref: "main"`, reusing `dispatchHeaders()` + `GITHUB_DISPATCH_TOKEN`. Mirror `dispatchOnDemandRun`'s 204-check error handling.
2. **`web/app/api/cron/tick/route.ts`** — new route mirroring `web/app/api/dispatch/route.ts`. Accept `POST` (and `GET`, since some external schedulers only do GET). Require server-only `process.env.CRON_TRIGGER_SECRET` via an `x-cron-secret` header (500 if env unset, 401 if missing/mismatch — same shape as `/api/dispatch`). On success call `dispatchScheduledTick()`, return `{ ok: true, dispatchedAt }`. No request body needed.
3. **`.env.example`** — add `CRON_TRIGGER_SECRET=` (server-only; do NOT add a `NEXT_PUBLIC_` twin). Document it must be set in Vercel **Production** env.
4. **`.github/workflows/search-scheduled.yml`** — keep `workflow_dispatch:` (already present). **Decision (ADR-052): keep `schedule: '*/15 * * * *'` as an explicit, commented degraded fallback** (if cron-job.org/route ever fails, scheduling degrades to "late", not "dead"). Add a comment line pointing at ADR-052 explaining the external trigger is the on-time path and the cron is the safety net. (Tradeoff acknowledged: the fallback still produces occasional ~hourly Actions runs; accepted for resilience.)
5. **UI copy truth-up** (land *with* the trigger, not before): in `web/app/[product]/ScheduleEditorButton.tsx` the past-time hint ("it will run at the next scheduler tick (within ~15 min)") and the save-success toast ("The scheduler will pick this up on its next tick.") are accurate once the external trigger is live — re-verify wording matches the new ~15-min reality; no change needed if still true. Add a one-line note to ADR-050's GitHub-cron caveat pointing to ADR-052.
6. **Manual, out-of-repo setup (runbook — must be documented because git can't see it; see ADR-052 + PROGRESS)**:
   - In Vercel → project → Settings → Environment Variables: add `CRON_TRIGGER_SECRET` (a long random string) to **Production**; redeploy.
   - At cron-job.org (free account): create a job — Title "product_search scheduler tick"; URL `https://<vercel-app-domain>/api/cron/tick`; method **POST**; add custom request header `x-cron-secret: <same value>`; schedule **every 15 minutes** (`*/15`); enable failure notifications; save & enable.
   - Record the job's existence + owning account in PROGRESS.md (config lives outside the repo — future sessions must know it exists).

**Done when**:
- GitHub Actions history shows `search-scheduled.yml` runs with **event = `workflow_dispatch`** arriving ~every 15 min, on time (within ~1–2 min of :00/:15/:30/:45), proven over ≥1 hour (≥4 consecutive on-time dispatches).
- End-to-end user scenario (the one that failed 3×): set a one-time `run_at` ~10–15 min out, confirm it fires within ~15 min, produces a report/CSV, and self-clears the `schedule:` block; the cards chip + detail footer (ADR-051) show the on-time run.
- Route security: returns 401 without/with-wrong `x-cron-secret`; 500 if `CRON_TRIGGER_SECRET` unset; success path dispatches exactly one tick.
- `npx tsc --noEmit` clean; `npm run lint` 0 errors (pre-existing warnings only).
- ADR-052 flipped PROPOSED → ACCEPTED (implemented); cron-job.org job + Vercel env var documented in PROGRESS.md.

**Out of scope**: replacing GitHub Actions as the executor; per-product external scheduling (one tick still fans out to all due profiles, unchanged); a dead-man's-switch / uptime monitor on the tick (noted as an optional later hardening in ADR-052); the Cloudflare Workers self-owned variant (documented as the considered-but-not-chosen alternative in ADR-052 — revisit only if cron-job.org proves unreliable or we want zero third-party schedulers).

---

## Phase 21 — Extraction reliability: hard-site render hit-rate (IN PROGRESS — see ADR-071)

> **UPDATE 2026-05-21 (R1/R2 done — this brief's escalation assumption was WRONG; read ADR-071 + docs/ALTERLAB_OPTIONS.md first).** Live measurement overturned the proposed "escalate to `min_tier:4`" plan: legacy `min_tier:4` *always* 202-hangs → body 0 (Target 0/3). The real fix is migrating the AlterLab request body to the **documented shape** (`location` + `cost_controls.max_tier` + `wait_condition`, keep `asp`), which scored Target detail **3/3** vs legacy **0/3**. T1 (`wait_for`→`wait_condition`) + a *safe* weak-render retry (no `min_tier:4`) are shipped; the documented-shape body migration is queued for next session pending sign-off. Tier escalation must use `cost_controls.max_tier`, NOT legacy `min_tier`. T2/T4/T5/T6/E1–E4 below otherwise still valid.

**Status of this brief**: design choices below are **PROPOSED**; the user asked for a plan to review async (stepping away 2026-05-21) and will confirm/adjust before implementation. Lock the chosen approach into ADR-071 at implementation time.

**Goal**: raise the hit-rate of `universal_ai` extraction against hard, bot-walled, JS-heavy vendors (Target, B&H, Best Buy, …) so that (a) the onboarder probe stops false-negatively dropping valid detail backups, and (b) scheduled/Run-now runs reliably return the correct live price instead of intermittently yielding 0. The user has explicitly approved **multiple fetch attempts and/or multiple URLs per vendor** as acceptable cost.

**Why (evidence from the 2026-05-21 prod re-onboard, ADR-070 follow-up)**: After fixing the missing `asp:true` (ADR-070), a live `sony-wh-1000xm5` onboarding showed extraction is **non-deterministic per URL/run**. In one run: B&H **Silver** detail (`1706394`) → `detailExtractable:true` (the asp fix demonstrably works — a B&H detail page passing the probe was impossible before), but Target detail, B&H **Black** (`1706293`), and B&H **Smoky Pink** (`1860582`) all → `false`. The `false`s were not the code bug — they were partial renders (Target's "There was a temporary issue" stub) or challenge pages returned with HTTP 200. Net effect: on an unlucky run the onboarder drops the ADR-067 detail backup, and a scheduled run can return 0 for a vendor that would have yielded on a retry. Separately, B&H runtime returned **body 0** with `wait_for:'5'` — a likely `wait_for` int-seconds-vs-CSS-selector bug.

**Root-cause framing (systemic, not per-vendor — ADR-067/068 ethos)**: the system fetches each URL **once** and trusts the first 200. The runtime's existing retry (`_fetch_html_with_retry`, ADR-053) only retries transient *exceptions*, not a 200-with-an-unusable-body. The onboarder probe doesn't retry at all. The fix is to treat "200 but the render is weak/blocked/empty-of-candidates" as a retryable condition with bounded escalation, and to harden the fetch params — applied uniformly via the vendor-quirks registry, not per profile.

**Tasks** (do research first; it's cheap and de-risks the rest):

1. **R1 — AlterLab API capability audit.** Read the AlterLab API docs (`api.alterlab.io` / `alterlab.io/docs`). Produce a short matrix of every render-quality knob and its cost: `wait` vs `wait_for` semantics (milliseconds? CSS selector? — this resolves the `wait_for:'5'` bug), `wait_until`/networkidle, scroll/`js_scenario`, `min_tier` tiers (what does 3 vs 4 actually buy — residential vs datacenter?), `country`, `session`, `block_resources`, any built-in retry. Output → a new `docs/ALTERLAB_OPTIONS.md` (or a section) + informs T1/T2.

2. **R2 — Quantify the baseline flakiness.** Probe the Target WH-1000XM5 detail URL + the 3 B&H variant detail URLs + the Target search URL **N=5 times each** with current registry options; record hit-rate (extractable / total) and body-size distribution. This is the before-number we improve against (evidence-based; ~20 AlterLab calls ≈ a few cents). Save representative good + bad rendered bodies as fixtures under `worker/tests/fixtures/universal_ai/`.

3. **T1 — Fix `wait_for` semantics** end-to-end (registry → adapter → probe → schema). Based on R1: if AlterLab `wait_for` is a CSS selector, registry numeric values (`wait_for:5`) are bugs that must become either a numeric `wait_ms`/`wait` field OR a real selector; add Pydantic + TS schema validation so a malformed `wait_for` can't be silently sent (it currently yields body 0). Regenerate web artifacts via `sync-prompt.js`.

4. **T2 — Retry-on-weak-render in the runtime adapter.** Extend `_fetch_html_with_retry` (or wrap `_fetch_via_alterlab`) with a "result-unusable" predicate: HTTP 200 but (body < a per-vendor expected floor) OR (challenge/footprint signature present) OR (0 candidates / detail not extractable). On unusable → retry with **bounded escalation** (cap ≈ 3 attempts, backoff): attempt 1 = registry defaults; attempt 2 = add wait/networkidle; attempt 3 = `min_tier` 4. Escalation runs ONLY on detected-weak renders, so the happy path (most pages succeed on attempt 1) costs nothing extra. Log every attempt + the final verdict (`applied_vendor_quirks`-style).

5. **T3 — Mirror the retry/escalation in the onboarder probe** (`probe-url.ts`) so onboard-time `detailExtractable` reflects the runtime's best effort, not one unlucky fetch. This is the direct fix for "the probe dropped a valid backup."

6. **T4 — Multiple URLs per vendor for multi-variant single SKUs.** Onboarder prompt update: for a single SKU sold as multiple variants (colors) on a stable-URL vendor, propose the search URL + the target-variant detail URL + (optionally, capped) sibling-variant detail URLs as separate `universal_ai_search` sources; runtime already merges + dedupes by canonical URL and takes the cheapest passing. Raises the odds ≥1 URL renders per run. Keep a cap (e.g. ≤3 detail URLs/vendor) for cost. No adapter change (multi-source already supported); prompt + possibly `vendor_quirks` (`force_detail_backup` → a variant hint) only.

7. **T5 — Probe↔runtime faithfulness guard (systemic anti-drift).** The missing `asp` (ADR-070) and the strip-method differences are symptoms of the TS probe being a hand-maintained parallel of the Python runtime. Add a **zero-cost unit test asserting the AlterLab request body the TS probe builds equals the one the Python adapter builds** for the same options dict (would have caught `asp` instantly), plus a fixture-based test that the TS strip→price-verify and Python strip→price-verify agree on the same saved HTML. Fail CI on divergence. (Both run offline against committed fixtures — no live calls, honors the no-live-slug rule.)

8. **T6 — Documented fallback for genuinely-walled vendors.** If R2/T2 show a vendor still can't be reliably rendered even with escalation (B&H may be Cloudflare-walled like microcenter — only Silver rendered), record it in `vendor_quirks.yaml` as a `known_failure`/`prefer_page_type` nuance so the onboarder routes it to `sources_pending` rather than the system retrying forever. "More reliable," not "infinite retries."

**End-to-end testing the implementer (Claude) runs itself this phase** (the user asked for full self-driven e2e incl. onboarding + running the slug; the user will be away):

- **E1** — Re-run R2's probe hit-rate measurement after T1–T3; show the improvement vs baseline (target: Target detail ≥4/5; B&H best variant ≥4/5, or documented as walled per T6).
- **E2** — Drive a **full onboarding via Chrome DevTools MCP** on a **throwaway test slug** (e.g. `wh1000xm5-e2e-test`, NOT the live `sony-wh-1000xm5` — do not clobber the good committed profile). Confirm the onboarder keeps Target search + detail backup and ≥1 B&H URL reliably.
- **E3** — **Save** the test profile (this commits to `origin/main`), then **Run-now** via the UI; poll the GH Action; read the committed `reports/<slug>/<date>.md` + per-run CSV. Assert ≥1 vendor reports the correct live price (Target ≈ $249.99 ± tax/discount) and the post-check found no fabricated price/URL.
- **E4** — **Delete the test slug** via the Phase 16 delete button and confirm `products/<slug>/` + `reports/<slug>/` are gone (honors the CLAUDE.md rule that no test/CI artifact may depend on a live slug). Capture any newly-seen live bodies as fixtures, strip session noise.

**Done when**:
- Target detail probe hit-rate materially up vs the R2 baseline (proposed bar: ≥4/5), measured and recorded.
- A fresh onboarding reliably keeps the ADR-067 detail backup (no silent drop on a single weak render).
- A Run-now/scheduled run on the test slug produces the correct Target price in the committed report, post-check clean.
- `wait_for` semantics corrected; the `wait_for:'5'` → body-0 class of bug is gone (schema-validated).
- Probe↔runtime AlterLab-request-body parity test (+ strip parity) in CI, green; guards against the next `asp`-style drift.
- ADR-071 (retry-on-weak-render + escalation + multi-URL policy) written; `vendor_quirks.yaml` updated for any confirmed-walled vendor; test slug deleted; PROGRESS updated.

**Cost guardrails / trade-offs to confirm (PROPOSED defaults)**: retries are bounded (≤3) and fire only on detected-weak renders, so steady-state run cost is ~unchanged; the worst case is ~3× one fetch for a genuinely flaky vendor. Multi-URL is capped at ≤3 detail URLs/vendor. E2E testing spends a handful of AlterLab calls + one Haiku onboarding + one search run (low single-digit cents). Confirm these caps are acceptable.

**Out of scope**: native per-vendor adapters (still Tier-A, separate); replacing AlterLab (separate evaluation); fixing the B&H *search-tile* walker (still deferred — detail URLs are the path for B&H).

---

## Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness (DONE — ADR-078/079/080)

**Goal**: implement the recall + precision fixes the user approved 2026-05-24, from the 3-product onboard+run eval (memory `project_recall_precision_eval_2026_05_24.md`): precision (Haiku filter) is excellent, recall is the bottleneck, dominated by fetch/extraction reliability and a few onboarder defects.

**Diagnostic confirmation (3 spaced isolated `cli probe-url` calls, 2026-05-24)**: AlterLab was degraded — Best Buy detail → 422 → curl_cffi ReadTimeout; Allbirds default render → 200/39-char stub; same Allbirds URL at `--min-tier 4 --wait-condition networkidle` → 200/1 MB/39 candidates (recovered). Confirms: AlterLab returns recoverable-but-degraded responses; the runtime escalation ladder fixes *weak 200s* but not a *raised 5xx* (which dropped to curl_cffi), and the probe-as-gate path is independent. All four items needed.

**Tasks (all implemented this session)**:
1. **R1 — retry AlterLab on transient 5xx before the curl_cffi fallback** (ADR-078). `_fetch_via_alterlab` retries a 500/502/503/504 with bounded linear backoff (≤3 attempts); 4xx still raise immediately.
2. **R6 — per-run circuit breaker + wall-clock budget** (ADR-078). `reset_run_state()` (called by `cli._cmd_search`); `_fetch_with_escalation` returns an `alterlab_degraded` flag; breaker opens after 3 consecutive degraded sources and short-circuits the rest; `_RUN_BUDGET_SECONDS` (default 600) is a second guard; skip reason surfaced in the Sources panel.
3. **R2/R3 — probe advisory + registry detail-preference enforced at the save gate** (ADR-079). `detail-preference.ts` + new `PREFER_DETAIL_HOSTS`; the gate keeps a registry detail-preferred source in `sources` (advisory note) on a probe failure instead of demoting; prompt forbids swapping a registry detail vendor to a search URL and mandates one deterministic demote-with-note policy.
4. **P1 — stop fragile `title_excludes`** (ADR-080). Prompt rule (no name-substring, no generic component word) + deterministic save-time soft warning (`title-excludes-check.ts`).

**Done when** (met):
- R1: a transient AlterLab 5xx is retried at the rendered tier before fallback; persistent 5xx exhausts retries then falls through; 4xx no-retry. (unit-tested)
- R6: breaker opens after N consecutive AlterLab-degraded sources, resets on a healthy fetch, and the budget skips remaining sources past the deadline; skips visible in the Sources panel. (unit-tested)
- R2/R3: a registry detail-preferred source survives a failing probe in `sources`; an ordinary source still demotes. (unit-tested)
- P1: a `title_excludes` substring-of-name warns at save; a disjoint value doesn't. (unit-tested)
- Green: worker ruff/mypy/pytest (305), web eslint/tsc/`test:parity`/`test:guards`/build. ADR-078/079/080 written; PROGRESS updated; 3 throwaway eval slugs deleted.

**Out of scope (deferred from the eval, not in this phase)**: R4/R5 + P2–P4 (any remaining eval items); routing hard constraints back through deterministic `filters.py` instead of `ai_filter` (now Phase 23); B&H search-tile walker (still deferred).

---

## Phase 23 — Hybrid filter restoration + headless e2e verification of Phase 22

> **READ THIS WHOLE BRIEF before touching code.** This brief is written to be self-contained for someone new to the repo. Do the start-of-session checklist in [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) first (read PROGRESS.md, then this brief, then the named ADRs). Don't open files outside the list below until you've read it.

**Two independent parts. Do Part A first** — it's verification, needs no design decision, and surfaces any Phase 22 regression early. **Part B is a small, already-signed-off code change.**

### Background you need (read these, in order)
1. **The decision that motivates Part B** — read ADR-001 (in [DECISIONS.md](DECISIONS.md): "LLM is downstream of verified data only") and ADR-075 (the `condition_in` deterministic filter). Then look at `worker/src/product_search/validators/pipeline.py` line 88: the comment literally says *"AI Filter (replaces deterministic filters)"*. That is the problem. The deterministic rejecters in `worker/src/product_search/validators/filters.py` (which already exist and are unit-tested) are **never called at runtime** — `ai_filter` (a Haiku LLM call) is the only thing deciding whether a "new only" / "in stock" / numeric-threshold requirement is honored. So a hard requirement is currently *model judgment*, not code. ADR-075 added `condition_in` to `filters.py` but it never actually runs.
2. **Phase 22, which you are verifying in Part A** — read ADR-078 (AlterLab 5xx retry + per-run circuit breaker/budget), ADR-079 (onboarder probe is advisory; registry detail-preferred vendors aren't demoted on a weak probe), ADR-080 (anti-fragile `title_excludes`). These all shipped 2026-05-24.

### Part A — Headless end-to-end verification of Phase 22 (do first; no sign-off needed)

**Goal**: prove the Phase 22 behaviors work against the *deployed* app, not just in unit tests. You drive a real browser yourself.

**Tooling**: use the **Chrome DevTools MCP** tools (`mcp__chrome-devtools__*`). They spawn their own Chromium — you do NOT need the user to drive the browser, and you do NOT run a local dev server. The deployed app is `https://ari-product-search.vercel.app`. (Background: see memory note "Chrome DevTools MCP available". Phase 21's E2–E4 did exactly this kind of self-driven onboarding; copy that shape.)

**Hard rule (do not skip)**: use a **throwaway slug** like `phase23-e2e-test`. NEVER touch a live slug (`sony-wh-1000xm5`, `dyson-v15-detect-vacuum`, etc.) — the deployed app commits profiles/reports directly to `origin/main` (see CLAUDE.md "Syncing with origin"). At the end you MUST delete the throwaway slug via the app's delete button so no test artifact is left on `origin/main` with a live cron (this is the CLAUDE.md "no test/CI artifact may depend on a live slug" rule; the 2026-05-24 eval violated it and left 3 slugs running).

**Steps**:
1. Open `/onboard` in the headless browser. Onboard a single-SKU product that exercises a registry detail-preferred vendor — suggestion: **"Logitech MX Master 3S mouse"** (it has a clean Best Buy / B&H detail story, and is the exact product whose `title_excludes` was fragile in the eval). Tell the onboarder "new only" (to exercise `condition_in`) and let it propose B&H + Best Buy.
2. **Verify ADR-079 in the transcript**: confirm the onboarder keeps B&H's `page_type:detail` URL (does NOT swap it to a search URL) even if a probe comes back weak, and says so. Take a screenshot of the draft `sources`.
3. **Verify ADR-080**: try to get the onboarder to emit a fragile exclude (e.g. say "I don't want the older MX Master 3"). Confirm it does NOT put `"MX Master 3"` in `title_excludes` (it should refuse / narrow it). If it does emit one, Save and confirm the **save-time warning** fires (the response surfaces a `warnings[]` entry from `title-excludes-check.ts`).
4. **Save** the throwaway slug (this commits to `origin/main`). Then **Run-now** from the product page. Poll until the GH Action completes.
5. **Verify ADR-078** by reading the committed run output / Sources panel for the slug: every `universal_ai_search` row either returned listings or shows a clear reason. If AlterLab was degraded during the run, confirm you see breaker/budget skip reasons (`"skipped: AlterLab circuit open…"` / `"…budget…exceeded"`) in the Sources panel rather than a silent 0. (If AlterLab is healthy that day, the breaker won't trip — that's fine; just confirm no source silently vanished.)
6. Read the committed `reports/phase23-e2e-test/<date>.md` + the per-run CSV. Confirm the post-check found no fabricated price/URL and that a "new only" run did not surface used listings.
7. **Delete** the throwaway slug via the home-page delete button. Confirm `products/phase23-e2e-test/` and `reports/phase23-e2e-test/` are gone from `origin/main` (`git fetch origin && git ls-tree -r --name-only origin/main | grep phase23` → empty).
8. If you captured any new live HTML bodies, save them as fixtures under `worker/tests/fixtures/universal_ai/` (strip session noise) per SESSION_PROTOCOL.

### Part B — Hybrid filter restoration (SIGNED OFF 2026-05-24: "Hybrid")

**The decision is made — do NOT re-debate it.** The user chose the **Hybrid** approach: deterministic `filters.py` rejecters run FIRST and are authoritative for the declared hard-constraint rules; `ai_filter` (Haiku) then runs on the survivors and keeps doing the thing `filters.py` *cannot* express — fuzzy semantic relevance (rejecting wrong models / variants / accessories for which there is no filter rule).

**Why this split**: `filters.py` already has a rejecter for every declared `spec_filters` rule type (`condition_in`, `in_stock`, `single_sku_url`, `title_excludes`, `form_factor_in`, `speed_mts_min`, `ecc_required`, `voltage_eq`, `min_quantity_for_target`) and a dispatcher `apply_filters(listing, rules, profile)` at `filters.py:142`. The only thing `ai_filter` does that has no rule is "is this even the right product?" relevance. So Hybrid = run `apply_filters` for the declared rules deterministically, then hand survivors to `ai_filter` for relevance.

**Exact change** (one file is the core):
1. In `worker/src/product_search/validators/pipeline.py`, in `run_pipeline` (line 78): BEFORE the `ai_filter` call (line 89), run a deterministic pass — for each listing call `apply_filters(listing, profile.spec_filters, profile)` (import it from `product_search.validators.filters`). Drop listings it rejects; keep the rejection reason. THEN pass the survivors to `ai_filter`. Delete/replace the misleading "replaces deterministic filters" comment.
2. **Keep `rejected_count` accurate**: it must now be `deterministic_rejects + ai_rejects`, not just the ai delta. Don't double-count a listing.
3. **Preserve rejection visibility**: the deterministic rejects should be attributable the same way ai_filter rejects are (check how `ai_filter` logs to `worker/data/filter_logs/` and the run output — match that so a user can see *why* a row was dropped, e.g. `[condition_in] condition 'used' not in ['new']`).
4. **`title_excludes` now runs deterministically (substring match).** This is exactly why ADR-080's save-time guard matters — a fragile exclude that is a substring of the product name would now genuinely zero recall. The guard already warns at save; that's the safety net. Do not soften the deterministic substring filter; rely on the ADR-080 guard + prompt rule to keep fragile values out.
5. **Don't change `ai_filter`'s job.** It can keep double-checking constraints in its prompt (harmless — deterministic catches them first), but its real remaining purpose is relevance. No prompt change is required; if you do touch its prompt, keep it focused on relevance and don't claim it's the sole gate.

**Tests (write with the code, in `worker/tests/`)**:
- A `used` listing with `spec_filters: [{rule: condition_in, values: [new]}]` is rejected **deterministically** even when `ai_filter` is stubbed to pass everything (monkeypatch `pipeline.ai_filter` to return all listings; assert the used one is still dropped). This is the regression that proves the fix.
- An out-of-stock listing with `in_stock` is dropped deterministically (same stubbed-ai_filter technique).
- A listing that passes all hard filters reaches `ai_filter` (assert it's in the input the stub received).
- `rejected_count` equals deterministic + ai rejects with no double-count.
- Existing pipeline tests stay green.

**Write ADR-081** documenting the Hybrid decision (Context: drift at pipeline.py:88; Decision: deterministic pre-filter authoritative for declared rules + ai_filter for relevance, signed off 2026-05-24; Consequence: hard constraints are deterministic again, ADR-080 guard becomes load-bearing for `title_excludes`). Add the one-line index entry.

**Done when**:
- `run_pipeline` runs `apply_filters` before `ai_filter`; a stubbed pass-all `ai_filter` can no longer let a `condition_in`/`in_stock`-violating listing through. (unit-tested)
- `rejected_count` correct; rejection reasons visible in the run output / filter log.
- Part A verification done and the throwaway slug deleted from `origin/main`.
- Green: worker ruff (`src/`) / mypy (`src/`) / pytest; web eslint / tsc / `test:parity` / `test:guards` / build. ADR-081 written; PROGRESS updated; commit + push.

**Out of scope**: changing `ai_filter`'s model or prompt structure; the remaining eval items (R4/R5/P2–P4); the B&H search-tile walker.

**Cost note**: Part A spends a few cents (one Haiku onboarding + a handful of AlterLab fetches + one search run) — acceptable per the user's cost stance (memory "Maximize recall over scrape cost"). Part B is offline/unit-tested, no spend.

---

## Phase 24 — Vendor-quirks coverage audit + Amazon JS-render fix (PROPOSED 2026-05-24)

> **READ THIS WHOLE BRIEF before touching code.** Do the start-of-session checklist in [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) first (PROGRESS.md, then this brief, then ADR-068 + ADR-071). Don't open files outside the list below until you've read it.

### Background you need (read these, in order)

1. **The proximate bug surfaced 2026-05-24 in Phase 23 Part A** — `phase23-e2e-test` run committed at `a1f98dc` had Amazon search + Amazon detail both `status: ok / fetched 0 / passed 0`. Two `cli probe-url` calls during that session confirmed Amazon's static HTML has 1.35 MB of body but 0 product-shaped anchors — product tiles are JS-rendered, and the source had no `extra.alterlab_options` so the runtime didn't request rendering. Saved YAML at `a1f98dc^:products/phase23-e2e-test/profile.yaml` is the evidence: Amazon sources have no `extra` block while Best Buy / B&H do.
2. **ADR-068** — vendor-level quirks live in [worker/src/product_search/vendor_quirks.yaml](../worker/src/product_search/vendor_quirks.yaml), not in per-profile YAMLs. The mechanism (`default_alterlab_options` merged under per-source options) is already in place: 7 vendors use it (Best Buy, B&H, Newegg, Microcenter, Target, Walmart, Williams-Sonoma). The adapter call site is `adapters/universal_ai.py:2516` (`merge_alterlab_options`).
3. **ADR-071** — only valid AlterLab wait-condition values are `domcontentloaded | networkidle | load`. The normalizer in [worker/src/product_search/vendor_quirks.py](../worker/src/product_search/vendor_quirks.py) (`normalize_alterlab_options`) auto-migrates the legacy `wait_for` field; do NOT reintroduce it.

### Diagnosis (already done, do not re-derive)

Three hosts are flagged as "needs AlterLab" but lack `default_alterlab_options` — they fetch at the bare datacenter tier and recall is silently 0 for JS-rendered content:

| Host | Current entry | Gap |
|---|---|---|
| amazon.com | `alterlab_known_good: true` only | needs `min_tier: 3, wait_condition: networkidle` |
| backmarket.com | `alterlab_known_good: true` only | likely same; PROBE FIRST to confirm |
| adorama.com | `force_detail_backup: true` only | likely same; PROBE FIRST to confirm |

### Tasks

1. **Fix 1 — Add Amazon defaults.** In `vendor_quirks.yaml`, upgrade the `amazon.com` entry to add `default_alterlab_options: {country: us, min_tier: 3, wait_condition: networkidle}`. Run `node web/scripts/sync-prompt.js` to regenerate `web/lib/onboard/vendor-quirks-data.ts` + `web/lib/onboard/promptText.ts`.
2. **Fix 2 — Audit Adorama + Backmarket.** Probe each with `cli probe-url <vendor search url> --min-tier 3 --wait-condition networkidle --country us` to confirm body size + anchor count come back materially higher than the bare path. Add `default_alterlab_options` to both with the values the probes validate (might be `min_tier: 4` for one of them; let the probe decide). Skip a host if its probe shows the bare path already works — don't add unnecessary cost.
3. **Fix 3 — Registry consistency check (the load-bearing safety net).** In `vendor_quirks.py` `_load_registry()` (or a new sibling validator called from it), iterate hosts: if `alterlab_known_good is True` AND `default_alterlab_options` is absent/empty, log a `WARNING` naming the host. This makes the next Amazon-class regression loud at import time, including during pytest collection. ~10 lines.
4. **Tests** — add to [worker/tests/test_vendor_quirks.py](../worker/tests/test_vendor_quirks.py):
   - `merge_alterlab_options("https://www.amazon.com/s?k=foo", None)` returns the expected default dict.
   - Source-level override wins: `merge_alterlab_options("https://www.amazon.com/...", {"min_tier": 4})` returns `min_tier: 4` with the other defaults intact.
   - Same shape assertions for adorama.com + backmarket.com defaults.
   - `caplog`-based test: a tiny in-memory registry with `{badhost: {alterlab_known_good: true}}` triggers the Fix-3 warning; a registry with `alterlab_known_good + default_alterlab_options` does NOT (negative case).
5. **Fixture-based extraction regression test.** During this session, run `python -m product_search.cli probe-url "https://www.amazon.com/s?k=logitech+mx+master+3s" --min-tier 3 --wait-condition networkidle --save-body worker/tests/fixtures/universal_ai/amazon_search_logitech_mx_master_3s.html`. Add a test (new file `worker/tests/test_amazon_extraction.py` or extend a relevant existing fixture test) that loads the body and asserts `_extract_candidates(html, base_url)` returns ≥5 product-shaped anchors, each with ≥1 price hint. This is the empirical proof the fix works on real Amazon HTML, frozen forever as a regression guard.
6. **One live `cli probe-url` validation pass through the runtime path** for amazon.com (no `--min-tier` flag — let `merge_alterlab_options` apply the new vendor_quirks default). Confirm body length > 50 KB and anchor candidates > 5. This is validation, not a test.
7. **ADR-082** — "Vendor `alterlab_known_good` tag implies JS-render defaults; gap is now lint-caught at registry load." Context (Phase 23 Part A Amazon evidence) / Decision / Consequence. One-line index entry in DECISIONS.md.

### Done when

- `vendor_quirks.yaml` has `default_alterlab_options` for amazon.com (and adorama.com / backmarket.com per probe-confirmed values).
- `vendor_quirks.py` warns at registry load for any `alterlab_known_good + no defaults` host.
- 6 new tests pass; all 280+ existing worker tests stay green.
- One live `cli probe-url` against the live amazon.com search URL via the runtime path returns body > 50 KB and ≥5 product anchors.
- ADR-082 written; PROGRESS.md updated; commit + push.
- Green: worker ruff/mypy/pytest; web eslint/tsc/`test:parity`/`test:guards`/build (the sync-prompt regen will touch `web/lib/onboard/*.ts`).

### Out of scope

- The AlterLab `browser_pool_exhausted` 422 we saw 2026-05-24 — that's an upstream infra transient; ADR-078's circuit breaker is the existing response. No code change here.
- The onboarder prompt's wording about Amazon JS rendering — vendor_quirks regeneration will refresh whatever it says. No prompt rewrite needed.
- B&H search-tile walker, Target search 0 candidates — remain deferred.
- A full N-vendor recall replay against live retailers — out of scope; the fixture test (task 5) carries the regression.

### Cost note

~$0.005 total: 3 confirming probes for the audit + 1 validation probe + ~1 fixture capture. No runtime cost change for already-configured vendors.

## Phase 25 — "Explain the zero": classified source-outcome reasons (+ AlterLab 422 transient retry) (PROPOSED 2026-05-24)

### Why
The report (rendered verbatim by the web via `ReactMarkdown`) is the only final output a user sees. Today a vendor with no results shows `ok / 0 / 0` or `error: <raw exception string>` in the "Sources searched" table — the user can't tell *genuinely empty* from *transient glitch* from *permanently broken* from *fixable on our side*. A bare "0" is far less useful than a reason. Driven by the 2026-05-24 session question about AlterLab's `browser_pool_exhausted` 422.

### Part A — `browser_pool_exhausted` 422 becomes retryable (ADR-083)
- **Root cause**: `_fetch_via_alterlab` ([universal_ai.py](../worker/src/product_search/adapters/universal_ai.py)) only retries on `status_code >= 500`; a 422 hits `raise_for_status()` and drops straight to curl_cffi (no JS/proxy) → bot-walled vendors return 0. ADR-078 lumped *all* 4xx into "a retry can't fix this" — true for 401/403/429/malformed, **wrong** for `browser_pool_exhausted`, a transient capacity error a backoff *can* clear.
- **Change**: before `raise_for_status()`, peek at the 422 body; if its error text matches `_ALTERLAB_422_TRANSIENT_MARKERS = {"browser_pool_exhausted"}`, route through the bounded-backoff retry path with a *longer* backoff (`_ALTERLAB_POOL_BACKOFF_SECONDS`, base 5s × attempt). All other 422s and 401/403/429 still raise immediately. Set a per-fetch module flag so Part B can label the outcome specifically.
- **Honest limit (in the ADR)**: in-run retries only recover *brief* exhaustion; a sustained outage still falls through — the ADR-078 circuit breaker remains the run-level guard, and Part B labels it `transient → likely resolves next run`.

### Part B — Source-outcome reason taxonomy + report callout (ADR-084)
New deterministic classifier `classify_source_outcome(...)` (new module `worker/src/product_search/source_reasons.py`, no LLM, no cli import). Five leaf categories → the user's four buckets:

| Category | When | Bucket |
|----------|------|--------|
| `NO_MATCH` | fetched>0, passed=0 | genuinely 0 (none qualified) |
| `EMPTY_PAGE` | substantive body, 0 candidates, body listing-free | genuinely 0 |
| `PARSER_GAP` | substantive body (≥`SUBSTANTIVE_BODY_FLOOR`), 0 candidates | resolvable w/ work |
| `TRANSIENT` | AlterLab degraded / pool-exhausted / 5xx / timeout / breaker- or budget-skip / generic fetch error | transient |
| `PERMANENT` | registry `known_failure`, or 401/403/429 quota/auth | unresolvable today |

- **Category-4 plumbing**: add `LAST_FETCH_DIAGNOSTICS` to `universal_ai` (reset per `fetch()` + in `reset_run_state()`, read right after `fetch()` in the source loop — same pattern as `LAST_SKIP_REASON`): `{body_len, final_status, final_fetcher, alterlab_degraded, alterlab_pool_exhausted}`. This is what separates `PARSER_GAP` from `EMPTY_PAGE` (a heuristic — stated as such in the ADR, worded cautiously).
- **Rendering**: keep the 4-column table unchanged; append a `> [!NOTE]`/`> [!WARNING]` **callout** listing only non-clean sources (passed==0 or error), one line each: category label + plain-English reason + whether/how it's fixable. Matches the existing `has_api_issue` block already in `_build_sources_searched_md` (fold that into the `PERMANENT` path). Integrate — don't duplicate — the existing `_build_filter_diagnostic_md` for `NO_MATCH` detail.

### Tasks
1. Part A retry + per-fetch pool flag in `_fetch_via_alterlab`.
2. `LAST_FETCH_DIAGNOSTICS` plumbing in `fetch()` / `reset_run_state()`.
3. `source_reasons.py` classifier + message builder.
4. Wire into `cli`: attach `skip_reason`/`diagnostics`/`host` to `source_stats`; render the callout in `_build_sources_searched_md`.
5. Tests (all fixture/mock, no live): 422 retry behavior; classifier per category; callout text; `LAST_FETCH_DIAGNOSTICS` population.
6. ADR-083 + ADR-084; PROGRESS.md update; commit + push.

### Done when
- Both parts implemented; both ADRs written; PROGRESS.md updated.
- New tests pass; full worker suite (314+) green; ruff/mypy clean on touched files; web `tsc`/`lint`/`test:parity`/`test:guards`/`build` green (no web code change expected).

### Out of scope
- Perfect `EMPTY_PAGE` vs `PARSER_GAP` disambiguation (heuristic + cautious wording).
- Auto-creating registry `known_failure` entries from runtime failures (stays manual).
- Bespoke web rendering — markdown callouts inherit via existing `ReactMarkdown`.

## Phase 26 — Cross-cutting LIVE stress test & regression sweep (Phases 20–25) (PROPOSED 2026-05-24)

### Why
Phases 20–25 layered in a lot of recall/robustness/transparency machinery — AlterLab render-path fixes, retry + circuit breaker, recall-first extraction, hybrid filtering, vendor-quirks defaults, the new source-outcome reason callout — but each was verified in isolation (unit tests + a single live probe/run). We have **not** driven the whole stack end-to-end across a *diverse, adversarial* set of vendors and products in one sweep. This phase is a deliberate, scenario-driven **live** stress test: onboard several throwaway products spanning the hard cases, run them, and confirm each improvement actually fires in production — and that the new report reasons tell the truth. The deliverable is a findings report + a defect/deferred list, not a feature.

### This is a LIVE, manual verification session — read first
- It costs real money (AlterLab + LLM). Rough budget **~$3–8** for the matrix below. If it starts ballooning past ~$10, stop and check with the user. The user prioritises recall over scrape cost (memory), so over-fetching to expose recall behaviour is fine — just be cost-aware at the run level.
- Use **throwaway slugs** with a clear prefix: `stress26-<short>` (e.g. `stress26-mx3s`, `stress26-xm5`, `stress26-ddr5`). Never touch a live slug. **Delete every throwaway slug at the end** (Phase 16 hard delete) and confirm live products are untouched.
- **Sync discipline (CLAUDE.md):** the deployed app + scheduled Action commit to `origin/main` on their own. `git fetch origin` and reason against `origin/main` before/after onboarding; the onboarder writes `products/<slug>/profile.yaml` directly to origin. Expect to `pull --rebase` around your own commits.
- **CI/test-suite rule still holds (ADR-062):** this session runs live, but do NOT add any dependency on a live `products/<slug>/` to the committed suite. If you find a regression, capture it as a committed fixture under `worker/tests/fixtures/` + a unit test, per the registry/fixture discipline — don't leave the proof in a live slug.
- **Chrome DevTools MCP is available** (memory: `reference_chrome_devtools_mcp`). Use it to drive the onboarder UI and to verify report rendering + **mobile layout** (CLAUDE.md: mobile is non-negotiable) instead of asking the user to click around.
- The LLM-never-fabricates commitment (CLAUDE.md / ADR-001) is itself under test: spot-check that every price/URL/stock figure in each report traces to fetched bytes.

### Background you need (read these, in order)
1. `docs/PROGRESS.md` (state) + this brief.
2. ADR index in `docs/DECISIONS.md` — open the bodies for the ADRs in the checklist below.
3. `worker/src/product_search/vendor_quirks.yaml` — the vendor registry you'll be stressing.

### Vendor matrix (pick URLs that exercise each row)
| Vendor | What it stresses | Expected behaviour |
|--------|------------------|--------------------|
| amazon.com | ADR-082 JS-render defaults, ADR-077 full-HTML extraction | tier3+networkidle merged; listings extracted |
| target.com | ADR-077 SPA full-HTML path (walker historically 0) | listings extracted from rendered DOM |
| bhphotovideo.com | ADR-079 detail-preference, new `PARSER_GAP` reason | search demoted/handled; detail URL works; if 0, reason = "needs work" |
| bestbuy.com | ADR-068 `intl=nosplash` transform, `force_detail_backup` | transform applied; detail backup present |
| microcenter.com | `known_failure` routing + new `PERMANENT` reason | lands in `sources_pending`; if run, reason = "blocked" |
| backmarket.com | ADR-082 defaults + possible Cloudflare | defaults merged; reason = transient/blocked if challenged |
| a clean server-rendered vendor (e.g. adorama / provantage) | JSON-LD baseline | listings via JSON-LD, no AlterLab needed |
| ebay (ebay_search) | dedicated adapter | listings via API/adapter |

### Product / scenario matrix (throwaway onboards)
1. **Single-SKU, "new only"** (e.g. Logitech MX Master 3S): stresses `condition_in` emission (ADR-075), fragile-`title_excludes` safety (ADR-080), detail backfill (ADR-076), multi-variant detail URLs (ADR-073).
2. **Multi-variant** (color/finish, same price — e.g. Sony WH-1000XM5): ADR-073 multi-variant detail redundancy; variant-correct pricing.
3. **Component/kit** (DDR5 RDIMM, the founding domain): kit pricing, numeric/threshold filters, QVL path.
4. **A product whose vendor set intentionally includes a known_failure** (force microcenter into the intent): proves `sources_pending` routing + the `PERMANENT` reason in a real report.

### Regression checklist — verify each fires in production
- **ADR-082 / ADR-068**: saved `products/<slug>/profile.yaml` carries the registry `default_alterlab_options` for amazon/backmarket; `intl=nosplash` applied for Best Buy. (Diff against `origin/main` after save.)
- **ADR-079**: a transient probe failure on a detail-preferred vendor does NOT demote it to `sources_pending` (it stays in `sources` with an advisory note).
- **ADR-080**: no `title_excludes` value that is a substring of the product name / generic component word in any saved profile.
- **ADR-075**: "new only" intent → `{rule: condition_in, values: [new]}` in the saved YAML AND visible in the run's filter log pass/reject reasons.
- **ADR-081 (hybrid filter)**: the deterministic pre-pass rejects used/out-of-stock/out-of-band BEFORE ai_filter — confirm in `reports/<slug>/<date>.filter.jsonl`.
- **ADR-076**: a `force_detail_backup` vendor saved with only a search URL gets a detail URL auto-backfilled by the post-save probe.
- **ADR-077**: Amazon/Target search yields listings (the full-HTML tier recovers what the anchor walker can't).
- **ADR-078 / ADR-083 (this is the headline new behaviour)**: if AlterLab is degraded/pool-exhausted during the session, confirm 5xx/422 retry fires and the circuit breaker short-circuits after the threshold — and that the report's **reason callout labels it `transient`**, not a silent 0.
- **ADR-084 (this session)**: for EVERY 0-result source across all runs, confirm the reason CATEGORY is correct — microcenter→`blocked`, a real parse miss→`needs work`, a transient AlterLab failure→`transient`, fetched-but-filtered→`no match`, genuinely empty→`no results`. This is the primary thing to validate.
- **ADR-020 / ADR-001**: post-check passes; every number in the report traces to fetched bytes (spot-check 2–3 listings per report against the source).
- **Web + mobile**: each report renders in the app incl. the callout; mobile viewport clean (Chrome DevTools MCP).
- **Phase 16**: throwaway slug deletion is clean and leaves live products intact.

### Procedure (suggested)
1. Read background; `git fetch origin`; note current live slugs (so you can prove they're untouched).
2. For each product scenario: onboard a `stress26-*` slug (drive the onboarder via Chrome DevTools MCP), record the saved profile, run the regression checks above against it.
3. Run each onboarded product (Run-now / `cli search`); capture the report + filter log; fill in the checklist.
4. Where AlterLab is healthy AND degraded during the window, capture BOTH a success and a degraded report so the reason taxonomy is exercised on real data.
5. Verify web rendering + mobile for at least 2 reports.
6. Write the findings report; file defects; clean up all `stress26-*` slugs.

### Deliverable
- `docs/STRESS_TEST_26.md` (or a clearly-named findings file): one row per checklist item — expected / actual / PASS|FAIL|N/A — plus a prioritised defect list. Each FAIL becomes either a committed fixture+test (if a code regression) or a new "noticed but deferred" entry / ADR candidate.
- Any genuinely cheap, in-scope fix discovered may be made inline; anything larger is written up, not implemented (one phase per session).

### Done when
- Every checklist row has a PASS/FAIL/N·A with evidence (report path, profile diff, or filter-log excerpt).
- All `stress26-*` slugs deleted; live products confirmed untouched; `origin/main` clean.
- Findings report committed; defects logged in PROGRESS.md "noticed but deferred" and/or DECISIONS.md.
- Worker suite + web guards still green (any new regression fixture/test added passes).

### Out of scope
- Implementing large fixes uncovered by the sweep — capture them; don't blow the session on a rabbit hole.
- New features. This is verification + regression only.
- Re-deriving decided ADRs — assume them correct and test that they HOLD, don't re-debate.

---

## Phase 27 — Fix the 3 Phase 26 defects + live re-verify (PROPOSED 2026-05-24)

### Why
Phase 26 ([STRESS_TEST_26.md](STRESS_TEST_26.md)) found three real defects in production: (D1) ADR-079's protection bypassable when the onboarder LLM drops a detail-preferred URL before save; (D2) ADR-084's callout silently swallows per-source `HTTPError` rows because the rendered `passed` count appears host-aggregated; (D3) `microcenter.com`'s `known_failure` registry entry is stale. Each is small on its own; they're bundled into one phase because they share infrastructure (live re-verify via throwaway `stress27-*` slugs, same MCP-driven pattern as Phase 26) and the bundle still fits one session.

### Ground rules (carry-over from Phase 26)
- **LIVE, costed session — budget ~$2–5**; stop and check with the user past ~$8. AlterLab + Haiku spend only.
- **Throwaway slug prefix `stress27-*`**; never touch a live slug; delete every one at the end via the Phase 16 hard-delete path; verify live products untouched on `origin/main`.
- **CLAUDE.md sync discipline**: `git fetch origin` and reason against `origin/main`; pull --rebase --autostash around your own commits.
- **CI/test-suite rule (ADR-062) still holds**: any regression captured as a committed fixture + test under `worker/tests/fixtures/`, never a `products/<live-slug>/` dependency.
- **Chrome DevTools MCP** is available (memory: `reference_chrome_devtools_mcp`); the orphan-profile-lock workaround from Phase 26 may bite — if so, ask the user to close the leftover MCP Chrome window (it surfaces as a one-line ask).
- **Vendor quirks** (ADR-068): registry change MUST come with a `sync-prompt.js` regen so `web/lib/onboard/{promptText.ts,vendor-quirks-data.ts}` track.
- **Push pre-authorized.** CLAUDE.md standing authorization applies.

### Background you need (read these, in order)
1. `docs/PROGRESS.md` (state) + this brief.
2. `docs/STRESS_TEST_26.md` — full evidence for each defect with file:line pointers and live-data quotes.
3. ADR-079 (`docs/DECISIONS.md`) for D1; ADR-084 (`docs/DECISIONS.md`) + `worker/src/product_search/source_reasons.py` for D2; ADR-068 + `worker/src/product_search/vendor_quirks.yaml` (microcenter entry) for D3.

### Defect 1 — make ADR-079 hard to bypass *(P1)*

**What's broken (from Phase 26):** on stress26-mx3s, the onboarder probed a B&H Photo detail URL, got `detailExtractable: false`, and **omitted the URL from the draft entirely** — leaving only a URL-less placeholder note in `sources_pending`. ADR-079's save-time gate (`web/lib/onboard/gate-universal-ai.ts`) only protects URLs that survive to `sources`, so the LLM's pre-emptive demotion bypasses the protection.

**Two-part fix:**

1. **Prompt rule** in `worker/src/product_search/onboarding/prompts/onboard_v1.txt` (regen → `web/lib/onboard/promptText.ts` via `node web/scripts/sync-prompt.js`): add a hard rule under the vendor-quirks knowledge map / probing-guidelines section — *"If a probe for a detail-preferred host (`prefer_page_type: detail` OR `force_detail_backup: true`) returns `detailExtractable: false`, you MUST KEEP the URL in `sources` with `extra.probe_note` set to a one-line summary. NEVER drop it to `sources_pending`. NEVER emit a URL-less placeholder note. The runtime escalation ladder + circuit breaker (ADR-078) own retry; one weak probe is not a reliable signal."*

2. **Deterministic save-time guard** (sibling to existing checks in `web/lib/onboard/`): a new pure helper `detail-preference-presence.ts` (import-free, callers pass host sets — same shape as ADR-079's existing `detail-preference.ts`). For each host in `PREFER_DETAIL_HOSTS ∪ FORCE_DETAIL_BACKUP_HOSTS`, if the draft has zero `universal_ai_search` sources for that host in `sources` (URL-bearing entries in `sources_pending` don't count), AND the host appeared in the chat's intended vendor list (or there's a URL-less placeholder for it in `sources_pending`), emit a soft warning. Wire it into `web/app/api/onboard/save/route.ts` alongside the existing ADR-067/074/080 checks. The save still proceeds — soft warning, same pattern as the others.

3. **Test**: add a case to `web/scripts/check-onboard-guards.test.mjs` (in CI) for the new helper — at minimum: a draft with a URL-less `sources_pending` for B&H Photo and zero B&H sources triggers the warning; a draft with a B&H detail URL in `sources` (with or without `probe_note`) does NOT trigger.

### Defect 2 — fix ADR-084 per-source `passed` accounting *(P1)*

**What's broken (from Phase 26):** on stress26-xm5, the Sources table rendered 3 rows for `bestbuy.com | error: HTTPError: ... HTTP/2 stream 1 was not closed cleanly | 0 | 2` — and the callout had NO bullet for any of them. Hypothesis: `source_stats[i].passed` carries a host-aggregated (or run-aggregated) total rather than the per-source count, so the classifier short-circuits at `passed > 0` → returns `OK` → no bullet.

**Fix:**

1. **Audit the search loop in `worker/src/product_search/cli.py`** (around line ~800, where `source_stats` rows are built). Specifically: confirm whether `source_stats[i].passed` is being overwritten with a host-level or run-level total before `_build_zero_reason_callout` reads it. The classifier itself at [`source_reasons.py:96`](../worker/src/product_search/source_reasons.py#L96) is correct given correct input.

2. **Fix the per-source accounting** so each row in `source_stats` carries ONLY the count of listings that passed from THAT source. (Likely a small refactor where a running total was bound to a name that's then attached to every same-host row.) Sources-table display benefits too — `Passed | 2` on a row that fetched 0 is genuinely confusing for users.

3. **Regression test in `worker/tests/test_cli.py`**: synthesise a `source_stats` shape mirroring the live xm5 case — one host appearing 4 times, one row `ok 4/2` and three rows `error: HTTPError ... 0/0`. Assert each erroring row's per-source `passed` is `0` and each lands in `_build_zero_reason_callout`'s output as a `transient` bullet. Naming idea: `test_build_zero_reason_callout_includes_per_source_httperror`.

### Defect 3 — re-probe microcenter and update the registry *(P2)*

**What's broken (from Phase 26):** stress26-mc's microcenter Ryzen 9700X detail URL extracted cleanly (`microcenter.com | ok | 1 | 1` at `$279.99`) via the Tier-1.5 detail extractor — yet the registry still marks microcenter `known_failure: blocker`. The onboarder consequently routes every microcenter URL into `sources_pending` and tells the user "this vendor is blocked", which is now a false negative.

**Fix:**

1. **Live probe ≥3 distinct microcenter URLs** at `country: us, min_tier: 3, wait_condition: networkidle` (the current registry defaults). Suggested: 1 search URL + 2 detail URLs across different product categories (e.g. a CPU, a motherboard, an SSD). Capture each `cli probe-url --render --detail` (or `--save-body`) output to evidence-file under `docs/`.
2. **Decision rule**:
   - ≥2 of 3 succeed → DOWNGRADE `severity: blocker` → `severity: warning` with an updated `summary` ("intermittent; succeeded for N/3 URLs on 2026-05-25 at tier 3 + networkidle; runtime breaker absorbs failures") AND remove the blanket `onboarder_action: "Put microcenter URLs in sources_pending"` so the onboarder can route to `sources` at its discretion.
   - 3 of 3 succeed → REMOVE the `known_failure` block entirely; keep the `default_alterlab_options`.
   - 0 or 1 of 3 succeed → KEEP the block (the Phase 26 success was a Cloudflare cache hit); add a note that it was re-checked.
3. **Regenerate web artifacts** via `node web/scripts/sync-prompt.js` (regenerates `vendor-quirks-data.ts` + the rendered knowledge-map section of `promptText.ts`).
4. **Cost**: ~$0.01–0.02 in AlterLab probes; trivial.

### Live re-verification (the "test as you did this time" step)

Mirror the Phase 26 stress pattern at smaller scale, ONLY targeting the three defect surfaces:

- **stress27-mx3s** (single-SKU "new only" Logitech MX Master 3S, same vendors as Phase 26 — Amazon, Target, Best Buy, B&H Photo, eBay). Drive via MCP. Expected outcomes:
  - **D1 PASS**: the saved profile has a B&H source in `sources` (with `probe_note` if the probe failed) OR triggers the new deterministic warning if the LLM still drops it. NOT an empty-URL `sources_pending` entry.
  - Re-run via Run-now; verify B&H either contributes listings OR shows up in the ADR-084 callout with a classified reason (rather than silently disappearing).
- **stress27-xm5** (multi-variant Sony WH-1000XM5, Best Buy + B&H + Target + Amazon, same as Phase 26). Drive via MCP. Expected outcomes:
  - **D2 PASS** (live): if AlterLab is degraded during the run and we get the same Best-Buy-detail HTTPError pattern, every failed Best Buy detail row now appears as a `transient` bullet in the ADR-084 callout. (D2 PASS doesn't require this — the unit test is the primary proof — but the live run is the confidence check.)
- **stress27-mc** (AMD Ryzen 7 9700X, same vendors as Phase 26 incl. Microcenter). Drive via MCP. Expected outcomes:
  - **D3 PASS**: after registry update, the onboarder routes microcenter into `sources` (not `sources_pending`) WITHOUT requiring the manual promotion step we did in Phase 26. Run-now produces a report including a microcenter row in the Sources table with `ok N/M`.

- **Mobile rendering check** at 375 px on the stress27-mx3s report — confirm the new `probe_note` doesn't break rendering and the callout still works.

### Done when
- All three defect fixes landed (prompt regen + new TS check + worker test + registry update with web artifact regen).
- Worker suite green (`pytest`, `ruff src/`, `mypy` on touched files); web `tsc` 0 errors, `eslint` 0 errors (pre-existing warnings only), `npm run test:parity` 2/2, `npm run test:guards` includes the new D1 check, `next build` compiled.
- Each of D1/D2/D3 has a live re-verification entry in a brief follow-up section appended to `docs/STRESS_TEST_26.md` (or a sibling `STRESS_TEST_27.md`, author's choice) — per-defect: expected / actual / PASS|FAIL + commit pointer.
- All `stress27-*` slugs deleted via the Phase 16 path; live products confirmed untouched on `origin/main`.
- ADR-079 amended (or a follow-up ADR added) noting the prompt+guard reinforcement; ADR-068 amended noting the microcenter status flip; ADR-084 amended noting the per-source `passed` fix. (Per `DECISIONS.md` discipline: append, don't rewrite existing ADR bodies — record the change as a new ADR or a status update on the original.)
- PROGRESS.md updated + Phase 26 state block archived; commit + push.

### Out of scope
- Newegg parser-gap investigation (Phase 26 Defect 6) — capture as a fixture if you stumble onto it; don't chase.
- `low_seller_feedback` flag description (Phase 26 Defect 5) — out of scope unless trivially in your way.
- The `spec_attrs.required` schema mismatch (Phase 26 Defect 4) — out of scope this session; queue as a separate small task.
- Phase 18 (second-product proof) — comes after Phase 27.

