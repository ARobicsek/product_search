# Next session — implement Phase 31 (Serper recall, end-to-end)

**Status:** READY TO CODE. This brief was written 2026-05-30 after a full read/analysis pass
of the code that Phase 31 touches. **No production code was written** (the session was cut short).
Everything below is the result of that analysis so you can start coding without re-deriving it.

**Read first:** PROGRESS.md, [REBUILD_PLAN.md](REBUILD_PLAN.md) (esp. §0, §3, §4, §9, §10), ADR-131/132/133.

---

## Phase 31 deliverable (REBUILD_PLAN §10)

> Generalize `Listing`; profile v2 schema + loader; `adapters/serper.py`; Serper-aware
> `single_sku_url`; `temperature=0`. **Recall works end-to-end on Serper.**

"End-to-end on Serper" for Phase 31 = a v2 profile's `queries` → the Serper adapter → a clean
`[Listing]` (live API **or** committed fixtures), with `buy_url` set and Serper-aware URL handling
so the listings are NOT 100%-rejected. The full run pipeline (price-sanity gate, diversity, synth,
display) is **Phase 32**; eBay wire-in is **Phase 33**. Don't pull those forward.

---

## ⚠️ One proposed deviation from REBUILD_PLAN §3 — surface to owner at session start

REBUILD_PLAN §3 says "**Rename** `unit_price_usd` → `price_usd`" and "**Move into `attrs`**:
`is_kit`, `kit_module_count`, `kit_price_usd`, `qvl_status`."

**Blast radius measured this session:** `unit_price_usd` = **234 occurrences across 39 files**;
the kit/qvl fields = **331 across 40 files** (incl. validators, synth, storage, diff, CSV, the
benchmark JSON fixtures, and every RAM test). A hard rename + field-move now lands mostly on
**RAM-pipeline code that Phase 36 deletes anyway** — high churn, high risk, throwaway work, and it
breaks the "keep core ~70% — do not touch beyond generalization" anchoring principle.

**Recommended (faithful to intent, low-risk):** do the generalization **additively**:
- Add `price_usd` as a **property** (get/set) aliasing the stored `unit_price_usd`. Generic
  Phase-32+ code and the Serper adapter use the generic name **now**; the destructive rename of the
  stored field lands in **Phase 36** when the RAM pipeline is deleted (churn then hits far fewer files).
- **Keep** `is_kit`/`kit_module_count`/`kit_price_usd`/`qvl_status` as fields. REBUILD_PLAN §3 itself
  offers "Keep kit fields **optional**" as the acceptable alternative to moving them into `attrs`.
  The Serper adapter sets neutral values (`is_kit=False, kit_module_count=1, kit_price_usd=None`),
  exactly as the spike/runtest did.
- **Add** the four new generic fields fully: `buy_url`, `image_url`, `rating`, `rating_count`.

This delivers the generalization's *intent* (generic code uses generic names; the new display fields
exist) without the destructive churn. **Flag this to the owner** (the plan is owner-signed; this is a
mechanics deviation, not a behavior change). If the owner wants the full hard rename now, it's a
mechanical find-replace + fixture/CSV/diff updates — budget the whole session for just that.

---

## The 5 deliverables — concrete plan

### 1. Generalize `Listing` — `worker/src/product_search/models.py`
- Add at the end of the dataclass (all default-valued, so field ordering stays valid):
  `buy_url: str | None = None`, `image_url: str | None = None`, `rating: float | None = None`,
  `rating_count: int | None = None`.
- Add a `price_usd` property:
  ```python
  @property
  def price_usd(self) -> float: return self.unit_price_usd
  @price_usd.setter
  def price_usd(self, v: float) -> None: self.unit_price_usd = v
  ```
- Add the four new fields to `to_dict()`.
- **Do NOT** change existing required fields to defaulted (the Serper adapter passes them explicitly).
- **Ripple check:** CSV is SAFE — `csv_dump.CSV_FIELDS` is an explicit tuple, so new dataclass fields
  are ignored by the round-trip. The risk is `to_dict()` adding 4 keys → exact-dict assertions in
  `test_report_json.py` / `test_synthesizer.py` / `test_storage.py`. Run those; if any assert an exact
  dict, update them to include the 4 nullable fields (legitimate shape generalization, not scope creep).

### 2. Profile schema v2 + loader — NEW `worker/src/product_search/profile_v2.py`
Build a **separate `ProfileV2` Pydantic model** (do NOT extend v1 `Profile` — v2 redefines `sources`
from a `list[Source]` to a dict, which clashes). v1 `Profile` stays 100% untouched; both coexist
until Phase 36. Reuse v1's `Target` and `Schedule` (import from `profile.py`) and v1's
`_resolve_profile_path` for `load_profile_v2(slug)` + add `load_profile_v2_from_path(path)`.

Discriminator: a **required** `schema_version: Literal[2]` field so a v1 YAML fails ProfileV2
validation (and Phase 34's unified loader can sniff it).

Sub-models (use `Field(default_factory=...)` for the model defaults):
- `MatchSpec`: `aliases: list[str] = []` (port v1's distinctiveness validator — digit OR multi-word),
  `title_excludes: list[str] = []`, `variant_strict: bool = True` (default true, §11 decision 1).
- `FiltersV2` (structured object, **minor deviation from the plan's list-of-single-key-dicts YAML —
  cleaner + typed for Phase 32 to consume; note in ADR**): `condition_in: list[str] | None = None`,
  `in_stock: bool | None = None`, `min_quantity: int | None = None` (honored for eBay; degraded for
  Serper — see §8 taxonomy).
- `SerperSource`: `enabled: bool = True`, `gl: str = "us"`, `num: int = 40`.
- `EbaySource`: `enabled: bool = False`.
- `SourcesV2`: `serper: SerperSource`, `ebay: EbaySource`.
- `DisplaySpec`: `max_listings: int = 20` (gt 0), `per_vendor_cap: int = 3` (gt 0), `attrs: list[str] = []`.
- `ProfileV2`: `schema_version`, `slug`, `display_name`, `description=""`, `product_type: str|None=None`,
  `target: Target`, `queries: list[str]` (min_length 1), `match`, `filters`, `flags: list[dict]=[]`
  (keep permissive — open flag set, Phase 32 types it), `sources`, `vendor_allowlist/blocklist: list[str]=[]`,
  `display`, `schedule: Schedule|None=None`, `alerts: list[dict]=[]` (keep permissive — Phase 35 types it).

### 3. `adapters/serper.py` — mirror `adapters/ebay.py` structure
- `fetch(query: AdapterQuery, *, fixture_path: Path|None=None) -> list[Listing]`.
- Fixture mode (`WORKER_USE_FIXTURES=1` or explicit `fixture_path`): tests pass `fixture_path` to a
  single committed fixture. Fixtures already exist: `worker/tests/fixtures/serper/*.json`
  (`ddr5_rdimm_ecc_32gb`, `dji_neo2_fly_more_combo`, `the_netanyahus`, `the_week_1yr_subscription`);
  top-level key is `"shopping"` (list).
- Live mode: `POST https://google.serper.dev/shopping`, header `X-API-KEY` from `SERPER_API_KEY`
  (env **or** `worker/.env` — copy the `_load_key()` loader from `worker/scripts/serper_spike.py`).
  Body `{"q": q, "gl": gl, "num": num}` per query in `query.queries`; `gl`/`num` from `query.extra`.
  Use `httpx` (consistent w/ ebay) — raise `SerperAuthError` (no key) / `SerperAPIError` (non-200).
- `_result_to_listing(r)` — port the proven mapping from `serper_spike.py`/`serper_filter_runtest.py`:
  - `source="serper_shopping"` (the ADAPTER id, NOT the merchant), `seller_name=r["source"]` (merchant).
  - `url = r["link"]` AND `buy_url = r["link"]` (the google.com/shopping redirect — the only link Serper
    gives; do NOT fabricate a merchant URL — honesty). `image_url=r.get("imageUrl")`,
    `rating=r.get("rating")`, `rating_count=r.get("ratingCount")`.
  - `unit_price_usd = parse_price(r["price"])` (regex parse "$1,234.00"; `0.0` if absent — price-None
    handling is the Phase 32 price-sanity gate).
  - `condition = ""` (honest unknown — **NOT** `"unknown"`; see pitfall below), `attrs = {}` plus
    `attrs["serper_product_id"] = r.get("productId")` for future dedup/merchant-resolution.
  - `is_kit=False, kit_module_count=1, kit_price_usd=None, quantity_available=None, brand=None, mpn=None,
    seller_rating_pct=None, seller_feedback_count=None, ship_from_country=None`.
  - Dedup by `productId` (fallback `link`) across queries.

  **PITFALL:** the spike's runtest used `condition="unknown"`, but `reject_condition_in` rejects a
  set-but-not-allowed condition — `"unknown" not in {"new"}` → it would reject **every** Serper listing
  when a profile has `condition_in:[new]`. Use `condition=""`; `reject_condition_in` already passes on
  empty (`if cond and cond not in allowed`). This is the "honored where known, else degrade honestly" rule.

### 4. Serper-aware `single_sku_url` (ADR-131 P0) — two places
- `validators/filters.py::reject_single_sku_url`: short-circuit `return None` when
  `listing.source == "serper_shopping"` (serper links are always offer redirects, never a vendor search
  page). Add a comment citing ADR-131 P0.
- `validators/ai_filter.py` system prompt, the `single_sku_url:` bullet: add "Serper/shopping listings
  use a `google.com/search` redirect link that is NOT a vendor search page — never reject a
  `serper_shopping` source for this rule." (The payload already sends `url`; consider also sending
  `source` so the model can apply this — currently it does not send `source` per listing.)

### 5. `temperature=0` through the LLM call (ADR-132) — `llm/__init__.py` + 3 providers
- Add `temperature: float | None = None` to `call_llm(...)` and forward to `_call(...)`.
- `_anthropic.py`: pass `temperature=temperature` to `client.messages.create` only when not None.
- `_openai.py`: `kwargs["temperature"] = temperature` when not None.
- `_gemini.py`: `generation_config["temperature"] = temperature` when not None.
- `validators/ai_filter.py`: set `temperature=0` in its `call_llm(...)` call.
- **Test note:** `test_ai_filter.py` stubs `call_llm` with `def _call(**_)` — accepts any kwargs, so
  adding `temperature` won't break it. Add a small unit test asserting `call_llm` forwards `temperature`
  to the provider `call` (monkeypatch the provider).

---

## Tests to add
- `worker/tests/test_serper.py` — fixture-mode `fetch` returns N listings; assert `source=="serper_shopping"`,
  `seller_name`==merchant, `buy_url`==`url`==serper link, `rating`/`rating_count`/`image_url` mapped,
  price parsed, `condition==""`, `attrs["serper_product_id"]` set, dedup works.
- `worker/tests/test_profile_v2.py` — load a v2 fixture profile; assert all blocks parse; bad
  `schema_version`/missing `queries`/non-distinctive alias raise `ValidationError`.
- Extend `worker/tests/test_validators.py` (or test_serper) — `reject_single_sku_url` returns None for a
  `serper_shopping` listing whose URL contains `search?`, but still rejects a non-serper search URL.
- LLM temperature-forwarding unit test (monkeypatch provider `call`).
- **New v2 fixture profile(s):** add under a NEW `worker/tests/fixtures/profiles_v2/<slug>/profile.yaml`
  (don't reuse the v1 `profiles/` dir). A DJI Neo 2 v2 profile pairs with the existing
  `serper/dji_neo2_fly_more_combo.json` fixture. Point `load_profile_v2_from_path` at it.

## Green-bar + close-out
- `cd worker && pytest` (was 420/420 at ADR-132) + `ruff check src` + `mypy src` must all pass.
- Recommended: reproduce CI's Python via `uv venv --python 3.12 .ci-venv` (see memory
  `reference_uv_match_ci_python`) before pushing.
- Update PROGRESS.md (mark Phase 31 done, set Phase 32 as next), append a Phase-31 ADR to DECISIONS.md
  (record the §3 additive-generalization + FiltersV2-as-object deviations), commit, push (pre-authorized).

## Files read this session (context map)
`models.py`, `profile.py`, `validators/{ai_filter,filters}.py`, `llm/{__init__,_anthropic,_openai,_gemini}.py`,
`adapters/ebay.py`, `storage/csv_dump.py`, `scripts/{serper_spike,serper_filter_runtest}.py`,
`tests/{conftest,test_ai_filter,test_openai_wrapper}.py`, `tests/fixtures/serper/*`, `tests/fixtures/profiles/*`.
