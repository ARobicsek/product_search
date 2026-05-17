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
