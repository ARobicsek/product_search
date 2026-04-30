# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 12 — Polish & second product proof** (live prod path proven; sub-phases queued)

See the Phase 12 brief in [PHASES.md](PHASES.md#phase-12--polish--second-product-proof).

## Status as of end of 2026-04-30 session (continuation 5)

**Per-product report columns, brand inference, synth retry, run-info
footer.**

Five code commits pushed to origin (plus one local at handoff —
`bb9b93e`, push pending):

1. **Per-product `report_columns`** (`d370c15`, ADR-025). Profile YAML
   may now declare a `report_columns: list[str]` from a 14-column
   registry (`rank, source, title, price_unit, total_for_target, qty,
   condition, brand, mpn, seller, seller_rating, ship_from, qvl_status,
   flags`). Default = legacy 8 columns when unset. Wired through:
   Pydantic schema → TS validator mirror → onboarding catalog →
   synthesizer column dispatcher. The Bose profile uses this to drop
   `qty` (always "unknown" for headphones) and surface `condition`,
   `brand`, `seller_rating`, `ship_from`. Live-tested on the
   `bose-nc-700-headphones` route — the table renders the chosen
   columns exactly.

2. **Edit-mode onboarding surfaces columns proactively** (`555b3ad`).
   The Sonnet onboarding chat, when started with a pasted existing
   profile, now MUST in its first reply: (a) acknowledge the profile,
   (b) explicitly list the current `report_columns` (or note the
   default), (c) show the full 14-id catalog, (d) ask what to change.
   No more "user has to ask before any column info appears".

3. **`brand_candidates` for missing-brand inference** (`b9d2ff6`,
   ADR-026). eBay Browse API doesn't reliably populate `brand` for
   non-RAM categories (headphones, peripherals). New optional
   profile field `brand_candidates: list[str]`; the validator
   pipeline (after ai_filter, before QVL/flags) runs
   `infer_brand_from_title(listing, candidates)` — case-insensitive
   word-boundary match, first hit wins, declared casing preserved.
   Existing non-None brands are not overwritten. Bose profile has
   `brand_candidates: [Bose]`.

4. **Synth retries once on `PostCheckError`** (`bb9b93e`, ADR-027,
   LOCAL ONLY at handoff). Both Haiku 4.5 and GLM 4.5 Flash
   occasionally fabricate percentages / savings amounts despite the
   prompt forbidding them. The retry's system prompt names the
   rejected numbers, gives explicit anti-pattern phrases, and
   restricts to qualitative phrasing only. If retry also fails, the
   original error propagates and `cli.py`'s stub-report path takes
   over. `PostCheckError` now carries `.bad_numbers: list[str]` so
   the retry can cite them.

5. **Run-info on the web UI** (`56c3a18` and `bb9b93e`):
   - Next to the Run-now button: caption `Last run: <duration> ·
     <relative time>` shown when no run is in flight (server-side
     fetch via new `getLastCompletedRun(product)` in
     `web/lib/dispatch.ts`).
   - **Below the report**: footer "Last run completed
     [absolute timestamp] · took [duration]". Renders red with the
     conclusion appended if the run failed. Reuses the same
     `lastRun` server fetch — no extra API calls.
   - Fixed a missed `<RunNowButton />` instance that wasn't
     receiving `lastRun` in the regular page path (only the
     empty-state path had it before).

98/98 worker tests pass; web tsc + eslint clean.

**Live state at handoff**:
- DDR5 profile: defaults; the deterministic table + GLM 4.5 Flash
  synth produce clean reports.
- Bose profile: custom 12-column set (most recent edit kept Brand
  and MPN columns; `brand_candidates: [Bose]` makes Brand show "Bose"
  instead of "unknown" in the next run).
- One observed failure during user testing: GLM emitted
  "saves 7.7%" → post-check rejected. The retry mechanism (pushed
  locally as `bb9b93e`) addresses this. **Push it before the next
  test run** or expect to see the same class of failure.

**Next session — start here:**

1. **Push `bb9b93e`** (synth retry + run-info footer) so the next
   run benefits from the retry and the user sees the bottom-of-page
   run footer.
2. **Trigger one live run on each product** to confirm:
   - Bose: Brand column shows "Bose" (not "unknown"); retry kicks in
     if synth fabricates again.
   - DDR5: still clean.
   - Both: bottom-of-page "Last run completed … · took …" footer
     renders correctly.
3. **If retry STILL doesn't catch all percentage fabrications**:
   options in order — (a) split Bottom line into a deterministic
   first sentence + LLM-supplied "and here's why" clause; (b)
   programmatically strip percentage tokens from LLM output before
   post_check (last resort — borders on hiding fabrications); (c)
   shorten synth's section list to just Flags + Context, drop
   Bottom line entirely.
4. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued. Pick one once two clean consecutive runs land on
   each product.

## Previous status (end of 2026-04-30 session, continuation 4)

**Synth swapped to GLM 4.5 Flash; workflows now commit on failure.**

This continuation tackled the two open items at the top of continuation 3.

1. **Synth model swap (config.py)**: `DEFAULT_SYNTH_PROVIDER` is now
   `glm`; `DEFAULT_SYNTH_MODEL` is now `glm-4.5-flash`. The
   URL-hallucination concern that justified ADR-019's swap to Haiku is
   gone — the 2026-04-30 synthesizer rewrite made the ranked-listings
   table deterministic, so the LLM no longer emits URLs at all (only
   Bottom line / Flags / Context). The post-check correspondingly
   only validates numbers (not URLs) since the rewrite. Phase 5
   benchmark scored GLM 4.5 Flash 10/10 on this same post-check at
   $0/run cost. The synthesizer extracts sections by regex from the
   LLM response, which tolerates a prose preamble even if GLM emits
   one. The OpenAI shim's `reasoning_content` fallback (from
   `bd4d005`) remains in place. ADR-024.

2. **Workflows commit on failure (search-on-demand.yml &
   search-scheduled.yml)**: added `if: always()` to "Commit and Push
   changes" in both. Paired with a new diagnostic-stub write in
   `cli.py`'s `PostCheckError` handler (writes the post-check error
   message + listing counts to today's report path before
   `sys.exit(1)`), so a synth failure now commits a useful diagnostic
   instead of leaving the stale prior-day report visible on the web
   UI. Without the stub, `if: always()` alone has no effect on a
   first-run-of-the-day failure (no report file would exist yet).

75/75 worker tests pass. Pushed: pending — local commit only.

## Previous status (end of 2026-04-30 session, continuation 3)

**The whole stack works. One known intermittency in synth.**

There were TWO on-demand runs after commit `33cf8db` (Haiku swap):

1. **Run `25174096193`** (15:27Z on `33cf8db`) — **SUCCESS**.
   - ai_filter (Haiku 4.5) kept 71 of 96 listings.
   - synth (Haiku 4.5) wrote a full report (Bottom line, 30-row
     ranked listings, Diff, Flags, Context).
   - post-check passed — no fabricated numbers.
   - bot committed `7f741a1`. **This is the report currently
     committed at `reports/ddr5-rdimm-256gb/2026-04-30.md`.**
   - Total wall-clock: ~1m40s (vs ~20m for the previous GLM run).

2. **Run `25174234732`** (15:30Z on `7f741a1`) — **FAILURE at synth
   post-check** with `fabricated numbers: ['169.54', '250']`.
   - ai_filter still robust: 69 of 96 listings kept.
   - synth Haiku invented `$169.54` and `250` in its narrative.
   - cli.py exited 1 → "Commit and Push changes" was skipped (no
     `if: always()`), so the run-1 report was preserved on disk.

The user-visible "Run finished with conclusion: failure" + stale
zero-pass report on the web UI is the run-2 stderr surfaced + an
edge-cache miss for the run-1 commit. The actual committed report
is the run-1 success.

So the situation is:
- ai_filter Haiku swap is unambiguously working.
- synth Haiku is intermittent. PROGRESS already flagged this:
  "Anthropic Haiku 4.5 still produces occasional savings figures
  (~20% of fixtures)". Observed rate today is ~50% (1 fail / 2 runs)
  on prod-scale data (69-71 listings vs ~10 in the fixture suite).
- Workflow doesn't commit when search exits 1 — that masks the
  failure mode behind a stale report.

**Next session — start here:**

1. **Trigger one live on-demand run** for `ddr5-rdimm-256gb` to
   confirm GLM 4.5 Flash synth produces a clean Bottom line / Flags /
   Context. Verify the committed report has a real ranked-listings
   table (deterministic) plus the LLM-supplied qualitative sections.
2. **If GLM regresses** (post-check rejects, or empty Bottom line),
   options in order: (a) tighten `synth_v1.txt` further; (b) revert
   to Haiku via `LLM_SYNTH_PROVIDER=anthropic` /
   `LLM_SYNTH_MODEL=claude-haiku-4-5` workflow env (no code change);
   (c) propose a new ADR that re-runs the Phase 5 benchmark against
   the simplified post-numbers-only post-check.
3. **Phase 12c (schedule editor UI) and 12b (Tier-B adapter) are
   still queued** behind a clean, deterministic prod path. Pick one
   once a clean GLM run is on disk.

### What shipped this continuation (commits 88c1bfd and 33cf8db, both on origin/main)

`88c1bfd` — full rule defs in ai_filter prompt + per-product filter
log committed alongside report + inline diagnostic block when 0
listings pass. ADR-022.

`33cf8db` — swap ai_filter from `glm/glm-4.5-flash` to
`anthropic/claude-haiku-4-5`. Parser walks from first `{`/`[` so a
prose preamble can't zero a run. New test pins it. ADR-023.

The diagnostic block from `88c1bfd` worked on its very first run —
it showed exactly that GLM-4.5-Flash was emitting "Let me analyze
the products one by one..." despite json_object mode. That observation
drove the Haiku swap in `33cf8db`. The whole arc — diagnose, build
diagnostics, observe, fix — completed in three commits over one
session.

### What shipped this continuation (uncommitted at handoff start; this commit captures it)

1. **ai_filter sends full rule definitions** —
   `worker/src/product_search/validators/ai_filter.py` was building the
   "Rules to apply" prompt block from `[r.rule for r in
   profile.spec_filters]`, which dropped every `values:` / `value:`
   field. Now uses `[r.model_dump() for r in profile.spec_filters]` so
   the LLM receives e.g. `{"rule":"form_factor_in", "values":["RDIMM",
   "3DS-RDIMM"]}` instead of bare strings. The prompt also now has an
   explainer per rule type so the LLM applies each rule against
   `attrs`/`title`/`url`/`quantity_available` with a consistent
   "unknown ≠ failed" semantic. The LLM payload now also includes
   `url` and `quantity_available` per listing (needed by the
   `single_sku_url` and `in_stock` rules respectively). See ADR-022.
2. **Per-product filter log committed alongside the report** —
   ai_filter now also writes `reports/<slug>/<date>.filter.jsonl`
   (truncating per call), one row per evaluated listing. This file is
   committed by the existing workflow `git add -A` step, so the next
   regression is debuggable from the public repo with no GH Actions
   auth needed. (Anonymous artifact downloads return 401 — that's why
   the previous session couldn't pull diagnostics directly.)
3. **Inline AI-filter diagnostic block in the report** — when
   `passed_listings == 0` and `all_listings > 0`, `cli.py` now appends
   a markdown table of the first 10 rejection reasons (or, on hard
   call-level failure, the first 600 chars of the raw LLM response).
   `ai_filter` exposes `LAST_RUN_LOG` and `LAST_RUN_RAW_RESPONSE` as
   module-level capture so cli.py can render this without re-reading
   the JSONL file.
4. **Test fixture extended** — `tests/test_ai_filter.py` autouse
   fixture now also monkeypatches `_per_product_filter_log_path` so
   pytest doesn't write to the real `reports/` directory.

74/74 worker tests pass. mypy delta on changed files is a single new
`list[dict]` type-arg notice that matches the existing pre-Phase-12
style (already tracked under "Noticed but deferred").

### What shipped earlier this session (all on origin/main)

1. **Web UI polling edge-cache fix** — `getReportContent` in
   `web/lib/github.ts` appends `?_cb=${Date.now()}` to the
   `raw.githubusercontent.com` URL because the GitHub raw CDN was
   serving stale reports past `revalidatePath`. The most recent run UI
   showed "Timed out waiting for run to complete" with the report
   already on disk — see "Open issues" below.
2. **Per-listing AI filter reasoning logs** — `ai_filter.py` writes
   one line per listing to `worker/data/filter_logs/<date>.jsonl`
   with title, price, url, source, pass, reason. Sentinel rows on
   filter failure: `index=-1, title="(filter call failed)", reason=...`.
3. **`ai_filter` parser robustness** — accepts four JSON shapes
   (`{"evaluations":[…]}`, `{"indices":[…]}`, bare-array variants).
   Loud `[ai_filter] ...` stderr prints on parse/shape failures.
4. **`ai_filter` prompt rewritten** — explicitly tells the LLM that
   unknown attributes are not failures; only reject when an attr is
   PRESENT and clearly violates a rule (or the title clearly
   contradicts). Mirrors the lenient semantic of the deterministic
   `apply_filters` it replaced (eBay adapter intentionally leaves
   `form_factor`, `ecc`, `voltage_v` as None).
5. **`ai_filter` model swap glm-5.1 → glm-4.5-flash** — confirmed
   prod failure mode: GLM-5.1 is a reasoning model, ignored
   `response_format=json_object`, dumped CoT prose into `content`.
   Switched to glm-4.5-flash (Phase 5 benchmark winner, non-reasoning,
   ~10x cheaper).
6. **`_openai.py` field-pick** — in JSON mode, picks whichever of
   `content` / `reasoning_content` actually parses as JSON, instead of
   only falling back when `content` is empty.
7. **Scheduled cron disabled** — `search-scheduled.yml` keeps only
   `workflow_dispatch`. Schedule editor UI is Phase 12c.
8. **Run diagnostics uploaded as workflow artifacts** — both
   `search-on-demand.yml` and `search-scheduled.yml` now upload
   `worker/data/filter_logs/` and `worker/data/llm_traces/` as a 14-day
   artifact named `run-diagnostics-<product>-<run_id>` (or
   `run-diagnostics-scheduled-<run_id>`).
9. **Pytest no longer pollutes the local filter log** —
   `tests/test_ai_filter.py` autouse fixture monkeypatches
   `_filter_log_path()` to `tmp_path`. The local
   `worker/data/filter_logs/2026-04-30.jsonl` (41 lines, all from test
   runs) is safe to delete before the next real run.

74/74 worker tests pass. Pushed: `2072708`, `6ee155d`, `d05523e`,
`dc34961`, `8726dc3` all on origin/main.

### Open issues for next session

1. **synth Haiku fabrication** — addressed in continuation 4 by
   swapping synth to `glm/glm-4.5-flash` (ADR-024). The
   URL-hallucination concern from ADR-019 is gone since the
   synthesizer rewrite made the table deterministic. Workflows now
   commit on failure (`if: always()`) and `cli.py` writes a stub
   report on `PostCheckError`, so any future regression surfaces on
   the web UI as a diagnostic block instead of a stale-cache
   surface.
2. **UI polling still times out.** Latest run before the prompt fix
   showed "Timed out waiting for run to complete" in red on the page
   even though the report committed. The cache-buster (`?_cb=`)
   targeted `getReportContent`, but the polling state machine likely
   also reads `getProductReports` (api.github.com — not raw CDN), or
   the action genuinely took longer than the polling timeout.
   Investigate `web/components/RunNowButton.tsx` polling timeout vs
   typical action duration (~3-4 minutes per the Actions UI history).

## Open follow-ups (deferred during this session)

- **CI on `main` is chronically red** on lint steps (worker ruff +
  web ESLint). Predates Phase 12; PROGRESS already tracked this as
  deferred. Worth a small cleanup pass — this session noted it
  again but didn't fix.
- **Phase 5 benchmark fixtures should be re-run against
  `anthropic / claude-haiku-4-5`** to formally re-confirm the synth
  picks 10/10 there too (per ADR-019). Not blocking; live data is
  proving Haiku works.

## Next session — start here

`ai_filter` is on Haiku 4.5 (ADR-023). `synth` is now on GLM 4.5
Flash (ADR-024) — same model that scored 10/10 in the Phase 5
benchmark on the same post-check at $0/run. The synthesizer rewrite
made the ranked-listings table deterministic, so the LLM no longer
emits URLs at all and the URL-hallucination risk that drove ADR-019
is gone. Both workflows now commit on failure (`if: always()`); a
`PostCheckError` writes a stub diagnostic report before exiting 1.

1. **Read this file.** Continuation 4 block at top is current state.
2. **Trigger one live on-demand run** for `ddr5-rdimm-256gb`. Verify
   the committed report has the deterministic ranked-listings table
   plus an LLM-generated Bottom line / Flags / Context.
3. **If GLM regresses**, options in order: (a) tighten
   `synth_v1.txt` further; (b) revert via workflow env vars
   `LLM_SYNTH_PROVIDER=anthropic` /
   `LLM_SYNTH_MODEL=claude-haiku-4-5` (no code change); (c) propose
   a new ADR.
4. **Investigate the UI polling timeout** ("Run finished with
   conclusion: failure" appears immediately when the Action exits 1
   but the page still shows the stale report — once the workflow
   always commits and writes a stub report on PostCheckError, this
   should self-resolve).
5. **Then** pick Phase 12b (Tier-B adapter), 12c (schedule editor),
   or cost tracking.

Useful housekeeping before the next run:
- `rm worker/data/filter_logs/2026-04-30.jsonl` if the local file is
  cluttered (CI runs don't depend on local state).

## Manual verification still needed for Phase 11

- Install PWA to iOS Home Screen on a real device, enable alerts, and trigger an on-demand run that produces a material diff to ensure iOS successfully receives the push.





## Open questions for the user

- Push notification "materiality" thresholds default to: any new cheapest path, ≥5% price
  drop, any new listing. User can override these in `products/<slug>/profile.yaml` under a
  future `alerts:` block.
- **GH Actions secrets** — the four LLM keys exist in `.env`; copy them to repo secrets before
  the next CI run: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY`.
- **Z.AI account balance** — As of Phase 10, the Z.AI wallet is topped up so
  `glm-4.6` and `glm-5.1` are now callable. Re-running the Phase 5 benchmark
  with these two models is on the deferred list. Onboarding (Phase 10) still
  picked Anthropic Sonnet 4.6 over GLM 5.1 (see ADR-015) because GLM has no
  hosted web-search tool — switching to GLM there would mean wiring an
  external search backend.
- **Gemini free-tier rate limit** — the benchmark hit 429 on the very first Gemini call.
  Either set up Vertex AI billing or drop Gemini from the slate for now.

## Blockers

None.

## Noticed but deferred
- **Pre-existing worker lint/type errors.** `worker` mypy still reports
  2 errors in `adapters/memstore.py` and `adapters/cloudstoragecorp.py`
  (Phase 6 files, `url: str | None` passed to `Listing.url: str`), and
  ruff reports ~11 issues across worker tests (mostly unused `pytest`
  imports). These predate Phase 10. Consider a small clean-up pass in a
  future session — they don't block CI today but would block a stricter
  pre-commit hook later.
- **No TS unit-test framework in `web/`.** The Phase 10 schema validator
  was sanity-tested ad-hoc via a one-off node script; it lives in
  `web/lib/onboard/schema.ts` and would be a natural first TS unit-test
  if/when we add Vitest. Not urgent because CI re-runs `cli validate`
  on every commit that touches `products/`.
- **`target.configurations` schema is RAM-shaped.** The required keys
  (`module_count`, `module_capacity_gb`) make sense for DDR5 but are
  awkward for non-RAM products. The Supermicro motherboard onboarding
  filled them as `{module_count: 1, module_capacity_gb: 1}` — validates
  cleanly but is semantically meaningless. Worth a generalisation pass
  in a future session: rename to `unit_count`/`unit_size`, make the
  shape opaque, or add a `target_kind` discriminator. Update the
  Pydantic model, the TS validator, the onboarding prompt, AND the
  existing DDR5 profile in one go.
- **Synthesizer post-check is strict by design and rejects calculated comparisons** like
  "X is 7.7% cheaper than Y" and "$80 savings vs Micron." This is per ADR-001 and caught
  real issues across all three working models. After one prompt iteration adding "do NOT
  compute new numbers," GLM 4.5 Flash went 10/10. Anthropic Haiku 4.5 still produces
  occasional savings figures (~20% of fixtures) — useful as a fallback only when prompted
  even more strictly. Prompt iteration here is ongoing, not a blocker.
- **Benchmark fixtures are committed but use the synthesizer payload shape directly.** If
  `build_input_payload` ever changes shape, the fixtures need regeneration via
  `python -m benchmark.fixture_gen --force`.
- The handoff mentions Reddit r/homelabsales as a Tier C source; it requires Reddit API
  credentials. Add to env when adopted.
- The local `.env` file contains real LLM keys. It's gitignored. If those keys have been
  shared anywhere outside this machine, rotate them.
- Phase 4 used `unit_price_usd` for the 5% diff threshold. `total_for_target_usd` (the
  "cheapest path to target" cost) is arguably more user-meaningful but is `None` for any
  listing whose capacity doesn't match a profile configuration. If/when we want target-cost
  diffs, add a second threshold or fall back gracefully when `total_for_target_usd is None`.

## Recently completed

- 2026-04-30 (continuation 4): Synth swapped to GLM 4.5 Flash;
  workflows commit on failure; `cli.py` writes a stub diagnostic
  report on synth `PostCheckError` before exiting 1. ADR-024.
  - `worker/src/product_search/config.py`:
    `DEFAULT_SYNTH_PROVIDER = "glm"`,
    `DEFAULT_SYNTH_MODEL = "glm-4.5-flash"` (was anthropic /
    claude-haiku-4-5 from ADR-019).
  - `.github/workflows/search-on-demand.yml` and
    `search-scheduled.yml`: added `if: always()` to "Commit and Push
    changes".
  - `worker/src/product_search/cli.py`: the `PostCheckError` handler
    now writes a stub report to today's report path with the error
    message, fetched/passed counts, and the sources panel before
    `sys.exit(1)`. Paired with `if: always()`, this makes synth
    fabrication failures surface as a diagnostic block on the web
    UI instead of a stale prior-day report.
  - 75/75 worker tests pass.

- 2026-04-30 (continuation 3): First clean live run since Phase 12
  started. Run `25174096193` on commit `33cf8db` produced
  `reports/ddr5-rdimm-256gb/2026-04-30.md` — Bottom line, 30-row
  ranked listings, Diff, Flags, Context, Sources. ai_filter passed
  71/96 listings; synth post-check passed. A SECOND run immediately
  after hit a Haiku synth fabrication (`['169.54', '250']`) and
  post-check correctly rejected it; that run's commit step was
  skipped, so the run-1 report stayed on disk. See "continuation 3"
  block at top for the full picture.

- 2026-04-30 (continuation 2): The diagnostic block from the previous
  commit caught GLM-4.5-Flash emitting prose preamble before JSON.
  Swapped ai_filter to `anthropic / claude-haiku-4-5` (already wired
  for synth) and made the parser walk from the first `{`/`[` so a
  stray sentence can't zero out a run. New test
  `test_tolerates_prose_preamble_before_json` pins it. See ADR-023.

- 2026-04-30 (continuation): Root cause for the ai_filter 0-pass
  mystery — prompt was sending only rule type names, never the values.
  See ADR-022. Filter log now committed alongside the report
  (`reports/<slug>/<date>.filter.jsonl`) so future failures are
  debuggable without GH Actions auth.

- 2026-04-30 (late session): Five-commit ai_filter debug arc — STILL
  RETURNS 0 PASSED IN PROD. See "Open issues" at top.
  - `2072708` — disabled scheduled cron (`search-scheduled.yml` keeps
    only `workflow_dispatch`).
  - `6ee155d` — `ai_filter` parser accepts four shapes (canonical
    object, legacy `indices` object, bare-array variants); writes
    sentinel rows on failure; loud `[ai_filter] ...` stderr.
  - `d05523e` — prompt rewrite teaching LLM that unknown attrs ≠
    failure; only reject on present-and-violating data or clear title
    contradiction. Eliminates the "all 95 reject because eBay adapter
    leaves form_factor/ecc/voltage as None" hypothesis.
  - `dc34961` — workflow upload-artifact step publishes
    `worker/data/filter_logs/` and `worker/data/llm_traces/` from each
    run. Test fixture redirects log writes to tmp_path so pytest
    stops polluting the local file.
  - `8726dc3` — confirmed via stderr that GLM-5.1 dumped CoT prose
    into `content` (visible: "The user wants to filter a list of
    products for DDR5 RDIMM ECC..."). Switched `ai_filter` model from
    `glm-5.1` (reasoning) to `glm-4.5-flash` (Phase 5 benchmark
    winner; honors `json_object`; ~10x cheaper). Hardened
    `_openai.py` to pick whichever of `content`/`reasoning_content`
    actually parses as JSON. New tests pin the field-pick logic.
  - **Despite all five commits, the run after `8726dc3` still
    reported 95 fetched / 0 passed.** Diagnostic artifact wasn't
    pulled before the user ended the session. Next session must
    download the artifact and inspect the actual GLM 4.5 Flash
    response per listing.

- 2026-04-30 (early session): UI polling cache-buster + AI filter reasoning logs.
  - `web/lib/github.ts:getReportContent` now appends `?_cb=${Date.now()}`
    to the `raw.githubusercontent.com` URL. The CDN was returning the
    stale report after `revalidatePath`, so polling refreshed against
    the old content and the Run-now button reset to idle while the
    new report was invisible. Cache-buster forces CDN revalidation.
  - `worker/src/product_search/validators/ai_filter.py` now asks
    GLM-5.1 for a per-listing evaluation (`pass` + `reason`) instead
    of just the passing indices. Every evaluated listing is appended
    to `worker/data/filter_logs/<date>.jsonl` (gitignored under
    `worker/data/`). Listings the model dropped from its response are
    logged with `pass=false, reason="no verdict returned by model"`.
    Backwards-compat preserved for the older `{"indices": [...]}`
    response shape. `max_tokens` bumped 4096→8192 to fit per-listing
    reasoning. 62/62 worker tests still green; web `tsc` clean.

- 2026-04-30: Phase 12a (Storefront silent-fail diagnostic & GitHub Actions push fix).
  - Fixed a race condition in `search-on-demand.yml` and `search-scheduled.yml` where pushing the generated report would fail with `[rejected] main -> main (fetch first)` if the repository was updated during execution. Added `git pull --rebase origin main` before `git push`.
  - Identified and fixed silent failures in `nemixram`, `cloudstoragecorp`, and `memstore` adapters. They previously returned an empty list `[]` on non-200 HTTP statuses. Changed to explicitly raise `RuntimeError`, allowing `cli.py` to correctly surface the error in the "Sources searched" report panel.
  - Fixed unit tests broken by Phase 12's introduction of `ai_filter`. Bypassed the LLM call in `ai_filter.py` when `WORKER_USE_FIXTURES=1` to keep tests deterministic and pass without requiring LLM credentials.
- 2026-04-30: Synthesizer Refactor (Deterministic Table Generation).
  - Eliminated the possibility of hallucinated links or malformed table formatting by shifting the responsibility of generating the "Ranked listings" and "Diff vs yesterday" sections from the LLM to deterministic Python code.
  - Simplified the `synth_v1.txt` prompt to only request the qualitative sections (Bottom line, Flags, Context).
  - Re-wrote `synthesizer.py` to extract those sections via regex and inject mathematically perfect Markdown tables built directly from the `Listing` objects.
  - Deleted complex URL verification regex from `post_check` since URLs are no longer processed by the LLM.
- 2026-04-30: Phase 12 (Universal AI Extraction and Filtering).
  - Designed and deployed a "best of both worlds" pipeline (ADR-021).
  - Replaced explicit CSS scraping with `universal_ai_search`, using GLM-5.1 to extract JSON from raw HTML.
  - Mitigated hallucination by strictly enforcing that LLM-extracted URLs exist verbatim in the source HTML.
  - Replaced deterministic `apply_filters` with `ai_filter`, offloading complex spec evaluations to GLM-5.1 before passing the surviving listing objects to Claude Haiku for report synthesis.
  - Set up persistent `.jsonl` trace logging for all LLM calls in `worker/data/llm_traces/`.
- 2026-04-29: Phase 12 wave 6 (Profile Edit Mode & Synthesizer Fixes).
  - Implemented the **Profile Edit Mode** in the Web UI. Users can now click "Edit Profile" on any product page, which loads the existing `profile.yaml` from GitHub and passes it into the Onboarding AI context. The AI can then apply natural language edits (e.g. "avoid 16GB cards").
  - Fixed GitHub Contents API PUT failing on overwrites by automatically fetching the existing file `sha` before committing.
  - Synced `title_excludes` down to the `web` validation schema, matching the python schema.
  - Reverted synthesizer LLM from GLM-5.1 back to `claude-haiku-4-5` to avoid overly verbose Chain-of-Thought output in the generated markdown. Added strict prompt rules explicitly forbidding planning text ("Analyze the Request", etc.) while condensing the `URL` and `Source` column into a single markdown hyperlink.
  - Fixed a classic clock-skew bug in `RunNowButton` polling where the Vercel server's dispatched timestamp was slightly ahead of GitHub Action's `created_at` timestamp, causing the frontend to wait indefinitely. Also added `run-name` to the dispatch workflow.

- 2026-04-29: Phase 12 wave 5 (stale-cache hotfix on the web side).
  - Wave 4 actually fixed the synth — the 2026-04-29 report on disk has
    a full bottom-line, 21-row ranked listings table, and sources
    panel. The Vercel page kept showing the empty-output diagnostic
    because `getReportContent` and `getProductReports` in
    `web/lib/github.ts` used `next: { revalidate: 3600 }`. The 1-hour
    data cache silently masked the first prod-data success.
    `revalidatePath('/[product]')` from `/api/revalidate` invalidates
    the route-segment render cache but not necessarily underlying
    data fetches without a tag. Switched both reads to
    `cache: 'no-store'` — a 10KB markdown report fetched from GitHub
    raw on every page load is fine for this app's volume.

- 2026-04-29: Phase 12 wave 4 (synth post-check canonicalisation).
  - **Smoking-gun finding**: even Claude Haiku 4.5 — well-documented for
    verbatim copy on tabular tasks — failed the post-check on a live
    eBay URL. The "fabricated URL" was identical to a payload URL on
    `scheme + host + path`; only the tracking query string
    (`?_skw=...&hash=item...&amdata=enc%3A...`) differed. The post-check,
    not the model, was wrong.
  - **Fix**: post-check now uses canonical URL comparison — scheme +
    lowercased host + path, with trailing slash stripped. Tracking
    params no longer cause false-positive "fabrication" errors. The
    strict guarantee on prices/quantities/MPNs is unchanged. ADR-020
    documents the refinement (does not supersede ADR-001).
  - **Diagnostic**: when the post-check now fails, the worker dumps the
    offending URL and its canonical form to stderr so the next failure
    is debuggable from the GH Actions log without code edits.
  - 2 new tests added; all 65 worker tests pass.

- 2026-04-29: Phase 12 wave 3 (synth provider swap).
  - **Confirmed root cause** of empty/garbage prod synth output via
    fresh GH Actions log: post-`bd4d005`, GLM 4.5 Flash *was*
    producing output (recovered via the new `reasoning_content`
    fallback) but its output included **hallucinated eBay URLs** with
    munged tracking parameters. ADR-001's strict post-check correctly
    rejected the output. Live eBay URLs have long
    `?_skw=...&hash=item...&amdata=enc%3A...` query strings; GLM
    isn't reproducing them verbatim.
  - **Switched synth default to `anthropic / claude-haiku-4-5`**
    (ADR-019, supersedes the model choice in ADR-012). Cost is
    ~$0.001/run; ANTHROPIC_API_KEY is already wired through both
    workflows. GLM remains supported as a provider for future
    benchmarking. The synth model is env-overridable
    (`LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL`) so reverting is one
    workflow edit.

- 2026-04-29: Phase 12 wave 2.
  - **Confirmed eBay live path works in prod**: 186 fetched, 160 passed
    after EBAY_CLIENT_ID/SECRET were added to GH Actions repo secrets.
  - **Fixed synthesizer choke on 100+ listings**: cap synth input to top
    SYNTH_MAX_LISTINGS=30 (sorted by total_for_target_usd) and bumped
    `max_tokens` from 2048 → 4096. The Phase 5 prompt was tuned against
    fixtures of ~5–10 listings; with 160 the LLM produced empty output,
    which passed the post-check (no fabricated numbers in nothing) and
    wrote a near-blank report. The full set remains in SQLite and the
    daily CSV; the report now appends a note when truncation applies.
    Empty-synth output is now caught explicitly (italicised note in
    place of the bottom line) instead of silently producing a blank
    report.
  - **Fixed sw.js Response.clone() bug**: SWR branch was cloning
    inside an async caches.open().then() callback after the page had
    already consumed the body. Cloned synchronously and excluded
    /api/* + non-GET requests from the SW cache.

  Open follow-ups from this session:
  - **Storefront adapters returning 0 in prod live mode**:
    nemixram_storefront, cloudstoragecorp_ebay, memstore_ebay all
    reported `fetched: 0` with no error. Each has a silent-fail path
    (e.g. nemixram returns `[]` on any non-200 from
    `/products.json`). Needs targeted diagnostic — possibly add error
    logging that surfaces to the sources panel, or capture a fresh
    fixture to compare against.
  - **CI on `main` is chronically red** on lint steps (worker ruff +
    web ESLint). Predates Phase 12; PROGRESS already tracked this as
    deferred. Worth a small cleanup pass.

- 2026-04-29: Phase 12 polish wave 1. Removed `WORKER_USE_FIXTURES: 1` from
  prod workflows (ADR-017); added deterministic "Sources searched" panel
  to reports (ADR-018); added elapsed-time + tighter polling to the
  Run-now UX; replaced Next.js boilerplate favicon with the custom PWA
  icon. Tier-B adapter, schedule editor UI, and manage-sources UI deferred
  to Phase 12a/b/c. Local commit; push pending.
- 2026-04-29: Phase 11 complete. Implemented iOS push notifications for alerts via PWA subscription flow, Upstash Redis storage, and `web-push`. Material diff detection integrated into worker `cli.py`.
- 2026-04-29: Unblocked live eBay adapter by securing Production API keys and successfully fetching live DDR5 listings. Set up VAPID keys, Upstash Redis, and environment variables for Phase 11. Implementation plan approved and ready for next session.
- 2026-04-28: Phase 10 complete locally. `/onboard` chat UI + streaming
  `/api/onboard/chat` (Anthropic Sonnet 4.6 with hosted web_search) +
  `/api/onboard/save` (TS-side Pydantic-mirror validator + GitHub Contents
  API commit). New env vars: `LLM_ONBOARD_*`, `GITHUB_CONTENTS_TOKEN`. Local
  commit; push + Vercel env var setup + live E2E test pending.
- 2026-04-28: Phase 9 complete and verified end-to-end on Vercel (https://ari-product-search.vercel.app). "Run now" on `/[product]` triggers a real GH Actions workflow_dispatch, polls run status, and refreshes the report when complete. Toolbar resets to idle once the RSC refetch lands.
- 2026-04-28: Phase 8 complete. Built the PWA shell in Next.js, added Tailwind typography, configured github fetch helpers, and established the list and product detail routes.
- 2026-04-28: Phase 7 complete. Implement `scheduler-tick` CLI command to orchestrate runs across profiles matching the current UTC hour. Created GitHub Actions workflows for hourly crons and on-demand workflow_dispatch runs. Local commit; push pending.
- 2026-04-28: Phase 6 complete. Tier A adapters (Shopify API + selectolax eBay stores).
- 2026-04-28: Phase 5 complete. Synthesizer (prompt + post-check), 10-fixture benchmark with
  six bar criteria, runner across five `(provider, model)` combos. Winner: GLM 4.5 Flash
  (10/10, $0/run). `cli search` now writes `reports/<slug>/<date>.md`. 19 new tests (60
  passing total). Local commit; push pending.
- 2026-04-28: Phase 4 complete. SQLite store, CSV dump, pure-Python diff engine, `cli diff`
  command, 13 new tests (41 passing total). Local commit; push pending.
- 2026-04-28: Phase 3 complete. Validator pipeline (filters, flags, QVL, total-for-target).
- 2026-04-28: Phase 2 complete. Listing model, LLM abstraction, eBay adapter (fixture mode).
- 2026-04-28: Phase 1 complete. Pydantic Profile model, validate CLI, 10 tests (ruff + mypy
  + pytest all green). Commit local — push + CI verification pending.
- 2026-04-28: Phase 0 complete. `worker/` skeleton, `web/` Next.js scaffold,
  `.github/workflows/ci.yml` created. All local checks green (2 smoke tests, ruff, mypy,
  ESLint, tsc). Commit local — push + CI verification pending.
- 2026-04-28: Initial planning scaffold written. PLAN.md, all docs/, .gitignore, .env.example,
  README.md, CLAUDE.md, product profile template, DDR5 profile + QVL.
- 2026-04-28: Decisions confirmed (ADRs 003, 004, 005 → ACCEPTED). Added ADRs 010 (iOS PWA +
  web push) and 011 (adapter authoring philosophy). Phase plan updated.
- 2026-04-28: Pushed planning scaffold to GitHub.
