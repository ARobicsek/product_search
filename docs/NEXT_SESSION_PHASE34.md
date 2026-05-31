# Next session — finish Phase 34 (onboarder v2 wiring)

**Read first:** `docs/PROGRESS.md` ("Most recent work" bullet), `docs/REBUILD_PLAN.md` §6 + §10.
**Branch:** `main`. **One phase per session.**

## ⚠️ Relocate the repo out of OneDrive first
This session ran in `C:\Users\ariro\OneDrive\Personal\Product search`. OneDrive's sync engine
intermittently **corrupted/swallowed file-content reads** (Read/cat/sed returned garbled or empty
content even after sync was paused), which made it unsafe to read+edit the large route/component
files — that's why the wiring was deferred. The repo is fully pushed to GitHub, so:
```
git clone https://github.com/ARobicsek/product_search C:\dev\product-search
cd C:\dev\product-search\web && npm install
```
Then run the wiring session there. (Test command output worked fine all session; only file-content
reads were corrupted — but don't risk editing 1700 lines against a flaky view.)

## What landed this session (committed, verified, INERT)
Four NEW files that **nothing imports yet** — the live onboarder is untouched and stays fully v1:
| File | Exports you'll call |
|---|---|
| `web/lib/serper.ts` | `serperShoppingPreview(query,{gl,num,apiKey})` → `{query,ok,count,items,error}` (never throws); `SerperItem` = `{title,merchant,link,price,priceText,rating,ratingCount,productId,imageUrl}`. Edge-safe (global `fetch`). |
| `web/lib/onboard/validation-v2.ts` | `validateProfileDraftV2(draft, originalSlug?)` → `{ok,errors,warnings,userWarnings,yamlText?,slug?}`; `renderProfileV2Yaml(draft, slug?)`; `aliasIsDistinctive(a)`. |
| `web/lib/onboard/promptTextV2.ts` | `promptTextV2` (the v2 system prompt). |
| `web/scripts/check-onboard-v2.test.mjs` | 17 guard tests; already in `package.json` `test:guards`. |

Verified: `test:guards` **77/77** (60 v1 + 17 v2), `tsc --noEmit` clean, `eslint` clean.

The worker side is already DONE: `cli.py search` routes `schema_version:2` profiles to `run_v2`
(Serper + optional eBay). A new/edited v2 profile runs end-to-end. Existing v1 live products stay v1
until re-onboarded.

## Owner-signed scope (don't re-ask)
- Live Serper preview = **model-invoked tool** (`serper_preview`); results also stream to the user pane.
- **Keep `web_search`** (model researches the exact product → better `queries`/`aliases`). No probing.
- Architecture = ADR-133 / REBUILD_PLAN §6 (already signed off).

## The wiring — file by file (must land together; a half-wired onboarder breaks /onboard)

### 1. `web/lib/onboard/prompt.ts` (trivial)
`loadOnboardPrompt()` currently returns `promptText` (v1). Switch the import + body to `promptTextV2`.
Keep `server-only`, the `\n\s*\n` cleanup, and the cached Promise. **(I made this exact edit this
session, verified it, then reverted it** so the foundation commit stayed inert — re-apply it as step 1.)

### 2. `web/app/api/onboard/chat/route.ts` (`runtime = 'edge'`, ~420 lines)
Keep the whole skeleton: auth (`WEB_SHARED_SECRET`/`x-web-secret`), `ANTHROPIC_API_KEY` check, message
parse/validate, `compressWithLedger` (KEEP_HEAD 1 / KEEP_TAIL 4), the SSE `ReadableStream`, the tool
loop with `shouldForceFinalize` (turn-budget) + `MAX_TURNS_PER_REQUEST 50` / `MAX_TOKENS 8192`, the
per-iteration `draft_update` streaming (from `validate_profile`'s `draft` arg AND from each `finalMsg`
text — ADR-114), and the `web_search`-can't-run-in-parallel guard (it returns a SYSTEM ERROR
tool_result when the model emits web_search alongside another tool).
**Change only the tools + handlers** (tool defs live ~line 157–230; handlers ~line 290–390):
- Remove the `probe_url` custom tool def + its `toolUse.name === 'probe_url'` handler branch + the
  `probeUrl` import + any `probe_result` SSE emission.
- Add a `serper_preview` custom tool: input `{ query: string, gl?: string }`. Handler →
  `const r = await serperShoppingPreview(query,{gl})`; **stream** `{type:'serper_preview', query,
  ok:r.ok, count:r.count, items:r.items.slice(0,12), error:r.error}`; return to the model a compact
  text summary (count + top ~20 `title | merchant | price`) so it can judge target-present +
  title_excludes coverage.
- Keep `web_search` (server tool `web_search_20250305`, `WEB_SEARCH_MAX_USES 10`).
- `validate_profile` handler → call `validateProfileDraftV2(draft, originalSlug)` (no
  `state`/`bypass` args). Return `ok` + `errors` to the model; still emit `draft_update`.
- Existing SSE events to PRESERVE (client consumes them): `meta`(slug), `tool_use`(name,input?),
  `tool_result`, `draft_update`(draft), `usage`, `text`, `done`, `error`, `turn_truncated`. **New:** `serper_preview`.

### 3. `web/app/api/onboard/save/route.ts` (`runtime = 'nodejs'`, `maxDuration 60`)
Keep auth + the alerts re-attach (edit mode: when `draft.alerts === undefined`, pull existing alerts
from the on-disk profile — unchanged). **Branch on `draft.schema_version === 2`:**
- v2: `const r = validateProfileDraftV2(draft, originalSlug)`; `!r.ok` → 422 `{error:r.errors[0],
  details:r.errors.slice(1)}`; else `commitNewProfile(r.slug, r.yamlText)`. **Do NOT** call
  `probeAndUpdateProfile` (retired for v2) → return `probeStatus:'skipped'`.
- Keep the v1 `draft`/`yaml` paths as fallback for one release, OR delete once the UI is v2-only.

### 4. `web/app/onboard/OnboardChat.tsx` (1280 lines — the big/risky one; read in ≤120-line windows)
- **Remove:** the save-time probe modal (component + state), the `/api/onboard/probe` fetch, the
  `chatProbesRef`/`probe_result` accumulation, `bypassForceDetailBackup`, the "Continue probing /
  Save and proceed anyway / Stop and save" affordances, and the v1 vendor-list draft preview.
- **Add:** handle the `serper_preview` SSE event → compact results panel (title, merchant, price,
  link). Render the **v2 draft** preview (queries / match / filters / sources.serper+ebay /
  vendor_allow+blocklist / display).
- **Change:** Save → single `POST /api/onboard/save` with `{ draft, originalSlug, state? }` (draft
  carries `schema_version:2`). Keep ADR-113 "auto-forward a 422 to the LLM" + the saveState
  reset-on-new-turn fix.

### 5. Retire (Phase-34 retirement target — REBUILD_PLAN §6/§10)
Stop using / delete once nothing references them: `web/app/api/onboard/probe/route.ts`,
`web/lib/onboard/probe-url.ts`, `web/lib/onboard/probe-and-update.ts`, and the probe-era guard
modules (`adr067-check`, `detail-title-match`, `alias-hallucination-check`, `title-excludes-check`,
`condition-drift-check`, `detail-preference*`, `gate-universal-ai`, `match-aliases-check`) +
`sources_pending`. The v1 `check-onboard-guards.test.mjs` / `validation.ts` / `promptText.ts` /
`onboard_v1.txt` / sync-prompt go when the v1 onboarder is fully removed — coordinate with Phase 36
(`universal_ai`/`vendor_quirks` cleanup) so you don't strip a file the v1 worker pipeline still needs.

## Deployment prereq
Add **`SERPER_API_KEY`** to Vercel env (the `serper_preview` tool needs it). `ANTHROPIC_API_KEY` is
already set for the chat route.

## Verification checklist (before commit/push)
- `cd web && npm run test:guards` + `npm run test:parity` green (add a v2-route/preview test if practical).
- `npx tsc --noEmit` clean; `npx eslint` clean on touched files.
- `npm run build` (`next build`) succeeds — it typechecks the whole project.
- Worker untouched → still 495/495 (only re-run if you touched `worker/`).
- Mobile: test `/onboard` at 375px before calling the UI done (non-negotiable).
- Log **ADR-137** (v2 onboarder); update PROGRESS.md; commit + push.
