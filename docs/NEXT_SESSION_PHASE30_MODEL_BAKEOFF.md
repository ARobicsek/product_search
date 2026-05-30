# Next session — Phase 30 filter-model bake-off (4 models × 4 products)

**One-line brief for the next session:**
> Working in product_search repo. Read docs/PROGRESS.md, then docs/NEXT_SESSION_PHASE30_MODEL_BAKEOFF.md and docs/STRESS_TEST_30.md. Run the 4-model filter bake-off across all 4 products and tell me which model to use in the migrated build. Keys below.

## Goal
Decide the **filter model for the migrated (Serper-recall) build**. We have run Haiku-4.5 and
GLM-4.6; this session adds **GPT-4o-mini** and **Gemini 2.5 Flash-Lite**, scores all four across
all four products on the same gold sets, and produces the recommendation.

## Everything is already built — this is a run-and-read session
- Harness: `worker/scripts/serper_filter_bakeoff.py` — self-sufficient (rebuilds each product's
  EXACT production ai_filter prompt from the **committed** Serper fixtures; no gitignored state
  needed). Sets `temperature` explicitly; scores determinism (Jaccard) + precision/recall/F1 vs
  gold + $/run; auto-runs only models whose key env var is present.
- **All 4 models smoke-tested live in the prep session and parse cleanly.** Important:
  **Gemini is routed via its OpenAI-compatible REST endpoint**
  (`https://generativelanguage.googleapis.com/v1beta/openai/`), NOT the `google-generativeai`
  gRPC SDK — the container's TLS interception kills gRPC (`CERTIFICATE_VERIFY_FAILED`). Already
  wired that way in the registry; nothing to change. Preliminary (book, temp=0): Gemini 18/18
  (P=R=1.00, flawless), GPT-4o-mini 8/33 (P=1.00 but **over-rejected**, R=0.44 — watch its recall),
  Haiku/GLM 18/18.
- Committed fixtures: `worker/tests/fixtures/serper/{ddr5_rdimm_ecc_32gb,dji_neo2_fly_more_combo,the_week_1yr_subscription,the_netanyahus}.json`.
- Committed fixture profiles: `worker/tests/fixtures/profiles/{ddr5-rdimm-256gb,dji-neo-2-motion-fly-more-combo,the-week-1yr-subscription,the-netanyahus-joshua-cohen}/`.
- Gold sets (`GOLD_PASS` in the harness): DDR5 + book are unambiguous; DJI + subscription encode
  the strict target-SKU / in-scope reading (the ADR-117 product decision — documented in the file).
- GPT-4o-mini + Gemini-2.5-flash-lite wiring was smoke-tested live in the prep session (parse OK).

## Setup (fresh container — keys do NOT persist; see "key persistence")
```bash
cd worker
uv venv .venv-spike --python 3.12 && . .venv-spike/bin/activate && uv pip install -e . pytest
cat > .env <<'EOF'
SERPER_API_KEY=<serper>
ANTHROPIC_API_KEY=<anthropic>
GLM_API_KEY=<glm>
OPENAI_API_KEY=<openai>
GEMINI_API_KEY=<gemini>
EOF
set -a; . ./.env; set +a
```
(Serper isn't needed for the bake-off itself — the fixtures are committed — but keep it for any
re-capture. The bake-off only needs the four model keys.)

## The run
```bash
# full 4×4 at temperature=0 (deterministic), 3 trials each, with the F1 matrix at the end:
PYTHONPATH=. python scripts/serper_filter_bakeoff.py --all --trials 3 --temperature 0
# add --show-rejects to eyeball WHICH titles a model drops on any single product:
PYTHONPATH=. python scripts/serper_filter_bakeoff.py --slug the-week-1yr-subscription --trials 3 --temperature 0 --show-rejects
```
GLM-4.6 is the slow leg (~60–90s/call, reasoning model) — run `--all` in the background and wait.

## What we already know (so you can interpret fast)
- **The non-determinism was a temperature bug** — prod `ai_filter` never sets `temperature`
  (default ~1.0). At `temp=0` Haiku & GLM are deterministic. **Ship `temperature=0` regardless.**
- **Precision is perfect (1.00) for Haiku & GLM on all 4 products** on title-only Serper data.
- **Recall is the variable.** GLM-4.6 beat Haiku on DDR5, tied on the book, but over-rejected the
  subscription AND its cost swung 10× ($0.0086→$0.0905/run) on ambiguous catalogs. Haiku flat ~$0.017.
- Owner's leaning was GLM-4.6; this session tests whether GPT-4o-mini (their research: "very high,
  strict JSON, cheap, small output") or Gemini-2.5-flash-lite ("medium-high, cheap") is a better
  precision/recall/cost/determinism balance — especially flat cost vs GLM's variance.

## Decision rubric (write the call explicitly in STRESS_TEST_30.md, log an ADR)
Pick the model that, across the 4 products: keeps **precision ≈1.0**, has the **best/most-stable
recall** (esp. on the subscription, the weak case), is **deterministic at temp=0**, and is
**cheap + flat-cost**. Tie-break toward flat, predictable cost and JSON-mode reliability. Then:
update `STRESS_TEST_30.md` with the 4×4 table + recommendation, log an ADR (e.g. ADR-132) for the
chosen filter model + the `temperature=0` fix, update PROGRESS, commit, push to main.

## Key persistence (why you must paste keys each session)
Secrets are **never committed** (CLAUDE.md hard rule), and the container is ephemeral, so
`worker/.env` does not survive. There is no way to persist them from inside the container. For
durable storage the owner must add them in the **Claude Code web environment settings**
(Environment variables / Secrets) — same place the migration will set `SERPER_API_KEY` +
`ANTHROPIC_API_KEY` for prod (GH Actions secrets / Vercel). Until then, paste the five keys into
the start prompt as shown above.
