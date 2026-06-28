"""AI-Aided Filtering for the validation pipeline."""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.config import filter_backend_config
from product_search.llm import LLMResponse, Message, call_llm
from product_search.models import Listing
from product_search.profile import Profile

# The fallback/default filter backend. Haiku is deterministic-enough at temp=0
# (ADR-132) and reachable from anywhere (incl. GitHub Actions), so it stays the
# default AND the fallback when the local box can't be used (ADR-147).
_HAIKU_PROVIDER = "anthropic"
_HAIKU_MODEL = "claude-haiku-4-5"

logger = logging.getLogger(__name__)


def _loud(msg: str) -> None:
    """Print to stderr so the message is visible in GitHub Actions logs.

    `logger.error` requires the action's log-level config to flush to stderr;
    a bare print is more reliable for prod-failure visibility.
    """
    print(f"[ai_filter] {msg}", file=sys.stderr, flush=True)


def _notify_filter_failure(slug: str, reason: str) -> None:
    """Best-effort operational alert when the local filter chain is exhausted.

    The owner wants a message when the local models fail in prod so they can
    investigate (ADR-147). Reuses the existing push bridge, which no-ops when
    ``WEB_URL``/``PUSH_NOTIFY_SECRET`` are unset (i.e. in dev) — so this only
    actually sends in prod. Wrapped so a notify failure never breaks a run.
    """
    try:
        from product_search.notify import notify_material_change

        notify_material_change(slug, f"Local AI filter failed ({slug}): {reason[:140]}")
    except Exception as e:  # pragma: no cover - notify is best-effort
        _loud(f"failed to send local-filter-failure notification: {e!r}")


def _looks_like_inner_eval(obj: object) -> bool:
    """True when ``obj`` is a single evaluation entry, not an outer envelope.

    A truncated outer envelope causes the walking parser to fall through to
    the first complete inner ``{"index":..., "pass":..., "reason":...}``
    object and return that — silently dropping every listing. We use this
    predicate to reject those false positives: the outer envelope is either
    a dict with ``evaluations``/``indices`` or a top-level array.
    """
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    return "index" in keys and ("pass" in keys or "reason" in keys) and "evaluations" not in keys


def _extract_json(text: str) -> object | None:
    """Return the outer JSON envelope embedded in ``text``, else None.

    First tries to parse the whole string. If that fails, walks from each
    ``{``/``[`` boundary and uses ``json.JSONDecoder.raw_decode`` to find
    valid JSON at that position. Skips matches that look like a single
    inner evaluation entry — those are the symptom of a truncated outer
    array, not a usable result. Tolerates models that prepend a prose
    preamble like "Let me analyze the products..." (observed with
    GLM-4.5-Flash, 2026-04-30) before the structured JSON.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if parsed is not None and not _looks_like_inner_eval(parsed):
        return parsed  # type: ignore[no-any-return]

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if _looks_like_inner_eval(obj):
            continue
        return obj  # type: ignore[no-any-return]
    return None


def _filter_log_path() -> Path:
    """Return today's filter-log file under ``worker/data/filter_logs/``."""
    # worker/src/product_search/validators/ai_filter.py -> worker/
    worker_dir = Path(__file__).resolve().parent.parent.parent.parent
    log_dir = worker_dir / "data" / "filter_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{datetime.now(tz=UTC).date().isoformat()}.jsonl"


# Env override mirroring ``PRODUCT_SEARCH_PRODUCTS_DIR`` (see profile.py): when
# set, the per-product filter log is written under
# ``$PRODUCT_SEARCH_REPORTS_DIR/<slug>/<date>.filter.jsonl`` instead of the
# repo's ``reports/`` tree. The test suite sets this to a tmp dir (autouse
# fixture in conftest.py) so running ai_filter with throwaway slugs
# (``test-product``, ``test-subscription``, the ``ddr5-rdimm-256gb`` fixture)
# no longer leaks ``reports/<slug>/`` dirs into the working tree. Unset in
# production, so scheduled runs are unchanged.
_REPORTS_DIR_ENV = "PRODUCT_SEARCH_REPORTS_DIR"


def _repo_reports_dir() -> Path | None:
    """Return the reports dir if discoverable, else None.

    Honors ``$PRODUCT_SEARCH_REPORTS_DIR`` first (tests / overrides); otherwise
    walks up from this file looking for the repo's ``reports/`` (next to
    ``products/``). Used to drop a per-run filter log alongside the committed
    daily report so the diagnostic survives even when GH Actions artifact
    downloads require auth the operator may not have. Returns None when no
    reports dir can be located (unusual CWDs).
    """
    override = os.environ.get(_REPORTS_DIR_ENV, "").strip()
    if override:
        return Path(override)
    # worker/src/product_search/validators/ai_filter.py -> repo root
    here = Path(__file__).resolve()
    for parent in [here.parent.parent.parent.parent.parent, *here.parents]:
        if (parent / "reports").is_dir() and (parent / "products").is_dir():
            return parent / "reports"
    return None


def _per_product_filter_log_path(slug: str) -> Path | None:
    reports = _repo_reports_dir()
    if reports is None:
        return None
    out_dir = reports / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{datetime.now(tz=UTC).date().isoformat()}.filter.jsonl"


# Module-level capture of the most-recent ai_filter run's per-listing verdicts.
# cli.py reads this when building the report so a 0-pass run can include
# the first-N rejection reasons inline. Reset at the top of every ai_filter call.
LAST_RUN_LOG: list[dict[str, Any]] = []
LAST_RUN_RAW_RESPONSE: str = ""

# Module-level capture of the most-recent ai_filter call's token usage so
# cli.py can render a Run cost panel covering ai_filter + synth without
# having to thread an extra return value through the validator pipeline.
# ``None`` after a fixture-mode run (no LLM call was made) or when the call
# bailed before reaching the API.
LAST_RUN_USAGE: dict[str, Any] | None = None


def reset_last_run() -> None:
    """Clear the module-level LAST_RUN_* capture.

    ``ai_filter`` resets these at the top of every call, but a pipeline run can
    legitimately SKIP the LLM entirely (a variant_strict exact-only run, or a
    run whose alias pre-pass left no remainder). In a multi-product scheduler
    tick the module globals would then carry the PREVIOUS product's verdicts/cost
    into the skipped product's report. Callers that may not invoke ``ai_filter``
    must reset first so the cost panel honestly shows "no LLM call".
    """
    global LAST_RUN_LOG, LAST_RUN_RAW_RESPONSE, LAST_RUN_USAGE
    LAST_RUN_LOG = []
    LAST_RUN_RAW_RESPONSE = ""
    LAST_RUN_USAGE = None


def _write_filter_log(slug: str, entries: list[dict[str, Any]]) -> None:
    """Append entries to the daily filter log AND truncate-write a per-product
    sibling under ``reports/<slug>/<date>.filter.jsonl`` so the diagnostic is
    captured in the committed repo (no auth required to inspect)."""
    timestamp = datetime.now(tz=UTC).isoformat()
    rows = [
        json.dumps({"timestamp": timestamp, "product": slug, **entry})
        for entry in entries
    ]
    try:
        with _filter_log_path().open("a", encoding="utf-8") as f:
            f.write("\n".join(rows) + ("\n" if rows else ""))
    except Exception as e:
        logger.warning(f"Failed to write daily filter log: {e}")

    try:
        per_product = _per_product_filter_log_path(slug)
        if per_product is not None:
            # Truncate per-run so the file reflects only the most recent
            # ai_filter call for this product on this date.
            with per_product.open("w", encoding="utf-8") as f:
                f.write("\n".join(rows) + ("\n" if rows else ""))
    except Exception as e:
        logger.warning(f"Failed to write per-product filter log: {e}")


# Per-call batch size. Each batch is one LLM round-trip; results are merged
# in the public entry point. Sized so 50 evaluations × ~120 chars each fit
# comfortably under ``_AI_FILTER_MAX_TOKENS`` even with verbose reasons.
# Pre-batching, a 144-listing run truncated the response mid-array and
# silently dropped every listing (2026-05-10 paintball-pistol incident).
_AI_FILTER_BATCH_SIZE = 50
_AI_FILTER_MAX_TOKENS = 16384


# Per-rule-type explanation blurbs. Only the blurbs for rule types actually
# present in the profile's spec_filters are emitted into the system prompt
# (relevance_check always applies and is emitted separately). Statically
# explaining a rule the profile does NOT carry primes the model to act on a
# non-existent constraint — most damagingly for ``condition_in``: with the
# explanation always present, the model fabricated "used"/"refurbished"
# rejections on titles that stated no condition, and even rejected on profiles
# with no active condition rule at all (Phase 41 / ADR-145 — the exact RAM part
# ``HMCG84AGBRA191N`` was dropped for a condition the profile explicitly allowed).
_RULE_EXPLANATIONS: dict[str, str] = {
    "condition_in": (
        "- condition_in {values:[...]}: ONLY reject when the title or URL EXPLICITLY contains a\n"
        "  condition word (e.g. 'used', 'pre-owned', 'open box', 'refurbished', 'renewed') AND that\n"
        "  condition is not in values. If the title states no condition word, this rule PASSES — never\n"
        "  infer a condition from the absence of one."
    ),
    "form_factor_in": (
        "- form_factor_in {values:[...]}: pass if attrs.form_factor is in values, OR if neither\n"
        "  attrs.form_factor nor the title indicates a specific form factor. Reject only when\n"
        "  attrs.form_factor is set to something not in values, OR the title clearly contains a\n"
        "  different form factor (e.g. \"UDIMM\" / \"SODIMM\" / \"LRDIMM\" in the title when the values\n"
        "  list does not include that form factor)."
    ),
    "speed_mts_min": (
        "- speed_mts_min {value:N}: pass if attrs.speed_mts >= N, or if speed is unknown.\n"
        "  Reject only if attrs.speed_mts is set and below N, OR the title clearly states a\n"
        "  lower speed (e.g. \"DDR5-4400\" or \"PC5-32000\" when min is 4800)."
    ),
    "ecc_required": (
        "- ecc_required: pass if attrs.ecc is true OR ecc is unknown. Reject only if attrs.ecc\n"
        "  is explicitly false, OR the title clearly says \"non-ECC\"."
    ),
    "voltage_eq": (
        "- voltage_eq {value:V}: pass if attrs.voltage_v equals V, OR voltage is unknown\n"
        "  (which it almost always is — voltage is rarely in titles). Only reject when\n"
        "  attrs.voltage_v is set and clearly != V."
    ),
    "min_quantity_for_target": (
        "- min_quantity_for_target: pass unless the listing definitely cannot hit the target.\n"
        "  Compare attrs.capacity_gb against the target configurations above. If\n"
        "  attrs.capacity_gb does not match any config's module_capacity_gb, reject. If\n"
        "  capacity is unknown, pass. If quantity_available is known and (quantity_available *\n"
        "  kit_module_count) is less than the required module_count, reject."
    ),
    "in_stock": (
        "- in_stock: pass unless quantity_available is explicitly 0 or negative. Pass when\n"
        "  quantity_available is null/unknown."
    ),
    "single_sku_url": (
        "- single_sku_url: reject only if the URL clearly points at a search results page\n"
        "  (e.g. contains \"/sch/\", \"search?\", or \"?_nkw=\"). Otherwise pass.\n"
        "  EXCEPTION: a \"serper_shopping\" source uses a google.com/search shopping-cluster\n"
        "  redirect link that is an OFFER, not a vendor search page — never reject a\n"
        "  \"serper_shopping\" listing for this rule (ADR-131 P0)."
    ),
    "title_excludes": (
        "- title_excludes {values:[...]}: reject if any string in values appears in the\n"
        "  title (case-insensitive substring match). Otherwise pass."
    ),
}

# Stable emission order so the prompt text is deterministic regardless of the
# order rules happen to appear in the profile.
_RULE_EXPLANATION_ORDER: tuple[str, ...] = (
    "condition_in",
    "form_factor_in",
    "speed_mts_min",
    "ecc_required",
    "voltage_eq",
    "min_quantity_for_target",
    "in_stock",
    "single_sku_url",
    "title_excludes",
)


def _build_system_prompt(profile: Profile, display_attrs: list[str] | None) -> str:
    """Build the ai_filter system prompt for ``profile``.

    Extracted from ``ai_filter`` so the prompt is inspectable in tests. The
    "How each rule type works" section lists ONLY the rule types the profile
    actually carries (plus the always-on relevance_check) — see
    ``_RULE_EXPLANATIONS`` for why statically explaining absent rules is harmful.
    """
    # Dump FULL rule definitions (rule type + values/value/etc.) — not just the
    # rule names. Earlier revisions stripped extras and sent only `r.rule`, which
    # left the LLM guessing what `form_factor_in` allowed, what `voltage_eq`
    # required, and which substrings `title_excludes` named. That guess
    # collapsed to "reject everything" in prod.
    rules_full = [r.model_dump() for r in profile.spec_filters]
    present_types = {r.get("rule") for r in rules_full}
    target_desc = f"Target: {profile.target.amount} {profile.target.unit}"
    if profile.target.configurations:
        cfgs = [c.model_dump() for c in profile.target.configurations]
        target_desc += f" (Need exactly one of these configs: {cfgs})"

    relevance_rule = {
        "rule": "relevance_check",
        "description": (
            "Must be the actual requested product, not an accessory or alternative. "
            "Reject wholesale, lot, or bulk listings (e.g. '25+ Copies', 'Lot of 10') "
            "unless the target specifically asks for multiple units."
        ),
    }
    rules_json = json.dumps([relevance_rule] + rules_full, indent=2)

    relevance_explanation = (
        "- relevance_check: reject if the item is clearly a pure accessory (e.g. water filters, cases,\n"
        "  replacement parts, batteries, chargers, wall mounts), a completely different product (e.g. V8 or V11\n"
        "  when V15 is requested), or incompatible.\n"
        "  IMPORTANT: You must PASS package additions, bundles, and cosmetic variations of the exact same\n"
        "  base model (e.g. if 'Dyson V15 Detect' is requested, pass 'Dyson V15 Detect Extra', 'Dyson V15 Detect\n"
        "  Absolute', 'Dyson V15 Detect Complete', or color variants like 'Yellow/Nickel'),\n"
        "  provided they contain the requested base model name."
    )
    how_lines = [relevance_explanation]
    how_lines += [
        _RULE_EXPLANATIONS[rt]
        for rt in _RULE_EXPLANATION_ORDER
        if rt in present_types and rt in _RULE_EXPLANATIONS
    ]
    how_section = "\n".join(how_lines)

    # ``description`` is optional (ADR-074 followup #2 — onboarder drafts
    # occasionally omit it). Fall back to ``display_name`` so the filter
    # prompt always has a coherent "what the user wants" line.
    description = profile.description.strip() or profile.display_name
    return f"""You are a product filter.
The user wants: {profile.display_name}
Description: {description}
{target_desc}

Rules to apply (each rule is a dict with a "rule" type and its parameters):
{rules_json}

How each rule type works (apply ONLY the rules listed above; do not invent others):
{how_section}

Decision rules:
- "pass": true means NO rule above is clearly violated.
- Unknown is NOT the same as failed. Apply each rule to the data you actually have
  (attrs, title, url, quantity_available). If a rule depends on an attribute that
  isn't present and isn't implied by the title, treat that rule as passed.
- Never reject for a condition, attribute, or rule that is not in the list above, and
  never reject on a value you had to infer — only an EXPLICIT cue in the title/url/attrs
  counts. If no condition_in rule is listed, condition is irrelevant: do not reject for it.
- The title is informative: "RDIMM ECC DDR5-4800 32GB" implies form_factor=RDIMM,
  ecc=true, speed_mts=4800, capacity_gb=32. Use those implications when applying
  rules.
- For each failure, name the specific rule and quote the offending substring from
  attrs/title/url so the human reviewer can verify.

The profile expects the following display attributes: {display_attrs or []}
Additionally, if you can clearly identify any of these common product attributes
from the title, extract them too: color, size, storage, material, edition,
pack_size, term, flavor, condition. Only extract when the value is UNAMBIGUOUSLY
present in the title — never guess.
For "condition", extract ONLY when the title explicitly states it, and normalize
to one of: "new", "used", "refurbished", "open box". Examples: "Brand New" /
"NWT" / "New with tags" / "Sealed" -> "new"; "Pre-owned" / "Gently used" -> "used";
"Renewed" / "Refurbished" -> "refurbished"; "Open box" -> "open box". Do NOT infer
condition from the absence of a cue — leave it out when the title is silent.
Extracting these attributes is for DISPLAY ONLY: an extracted value (including a
condition) must NEVER by itself cause "pass": false. Extraction and the pass/fail
verdict are independent — only an active rule above can reject a listing.
If any of these attributes can be clearly extracted, add them to a new
"extracted_features" dictionary in your evaluation object for that listing. For example:
"extracted_features": {{"color": "black", "condition": "new"}}.

You will receive a JSON list of products. Output a JSON object with a single key
"evaluations" containing an array with one entry PER PRODUCT, in input order. Each
entry must have these keys:
  - "index": integer, matching the input index
  - "pass": boolean
  - "reason": short string (1 sentence) — REQUIRED ONLY when "pass" is false: name
    the specific rule that failed and quote the offending word from attrs/title/url.
    OMIT "reason" entirely for passing items (pass:true) — do not explain passes.
  - "extracted_features": (optional) object, only if display attributes were requested and found.

Every input product must appear exactly once in "evaluations". Do not omit any.
IMPORTANT: Do NOT output any chain-of-thought or reasoning text outside the JSON.
ONLY output the JSON object.
"""


# JSON schema for the filter response, used for schema-constrained decoding on
# the local (llama.cpp) backend (ADR-147). Forcing this structure eliminates the
# "reasoning model leaks chain-of-thought with raw newlines into a string field"
# parse-failure class that made qwen-coder unreliable on the hard RAM batches.
# ``reason`` + ``extracted_features`` stay optional so the model can omit a
# pass-reason (ADR-142) and so the Phase-37/40 smart-card attributes survive.
_EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "pass": {"type": "boolean"},
                    # The length bound is load-bearing on the local backend: the
                    # grammar enforces it, capping the reason so the model can't
                    # ramble an unbounded chain-of-thought that runs past
                    # max_tokens and truncates the JSON (Phase 42 / ADR-147). It
                    # also matches the prompt's "short string (1 sentence)".
                    "reason": {"type": "string", "maxLength": 240},
                    "extracted_features": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["index", "pass"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evaluations"],
    "additionalProperties": False,
}


def _resolve_filter_chain() -> list[tuple[str, str]]:
    """Return the ordered ``(provider, model)`` fallback chain for this run.

    Honors ``AI_FILTER_BACKEND`` (config.py). Default (``anthropic``) → just
    Haiku. For ``local`` it consults the polite shared-box coordinator (ADR-147):
    if it grants the box, the chain is the primary local model followed by the
    secondary local model (``LOCAL_LLM_FALLBACK_MODEL``, default qwen3.6-27b-mtp)
    — a same-box safety net for the rare batch the primary can't produce. If the
    box is unreachable or stays busy, the chain is just Haiku (the reachability
    fallback, so a run never hangs). Per the owner (ADR-147), the in-run quality
    fallback is LOCAL-only — Haiku is reserved for the box-unavailable case.
    """
    cfg = filter_backend_config()
    if not cfg.is_local:
        return [(_HAIKU_PROVIDER, _HAIKU_MODEL)]
    from product_search.llm.local_box import coordinate_local_access

    chain = [("local", cfg.local_model)]
    if cfg.local_fallback_model and cfg.local_fallback_model != cfg.local_model:
        chain.append(("local", cfg.local_fallback_model))

    if coordinate_local_access(cfg, log_fn=_loud):
        return chain
    # Box unavailable/busy past the polite wait. In DEV (allow_haiku_fallback)
    # fall back to Haiku so the run never hangs. In PROD (owner: cost ~0, no
    # Haiku) stay local-only and proceed on the box anyway — a total failure
    # then notifies instead of paying for Haiku (ADR-147).
    if cfg.allow_haiku_fallback:
        return [(_HAIKU_PROVIDER, _HAIKU_MODEL)]
    _loud("local box unavailable but Haiku fallback disabled - proceeding local-only")
    return chain


def _call_filter_llm(
    provider: str, model: str, system_prompt: str, payload: list[dict[str, Any]]
) -> LLMResponse:
    """One filter round-trip. ``cache_system`` is Anthropic-only (ADR-142);
    ``json_schema`` engages schema-constrained decoding on the local backend."""
    return call_llm(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        system=system_prompt,
        messages=[Message(role="user", content=json.dumps(payload, indent=2))],
        max_tokens=_AI_FILTER_MAX_TOKENS,
        response_format="json",
        # ADR-132: deterministic filtering. At provider-default (~1.0) Haiku's
        # pass-count swung 35/28/19 on identical input; temp=0 makes it
        # near-deterministic, and the local models are fully deterministic
        # at temp=0 (ADR-145).
        temperature=0,
        cache_system=(provider == _HAIKU_PROVIDER),
        json_schema=_EVAL_SCHEMA,
    )


class _BatchError(Exception):
    """A single filter batch failed (call error, JSON parse, or bad structure).

    Carries a human ``reason`` for the sentinel/log. Raised by
    ``_call_and_parse_batch`` so the caller can apply a single local->Haiku
    fallback for ALL failure modes — not just call exceptions. The Phase-42 live
    A/B (ADR-147) showed qwen-coder can emit a truncated/unparseable batch on the
    larger inputs, which previously zeroed the whole run; routing parse failures
    through the same Haiku fallback as connection errors closes that gap.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _call_and_parse_batch(
    provider: str, model: str, system_prompt: str, payload: list[dict[str, Any]], batch_len: int
) -> tuple[list[dict[str, Any]], LLMResponse]:
    """Call the model for one batch and return ``(batch_evals, response)``.

    Normalizes the several accepted response shapes (``{"evaluations":[...]}``,
    ``{"indices":[...]}``, a bare evaluation array, or a bare index array) into a
    list of ``{"index","pass","reason"}`` dicts. Raises ``_BatchError`` on a call
    exception, a JSON parse failure, or an unrecognized structure.
    """
    try:
        resp = _call_filter_llm(provider, model, system_prompt, payload)
    except Exception as e:
        raise _BatchError(f"ai_filter exception: {e!r}") from e

    raw_text = (resp.text or "").strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()
    raw_text = raw_text.removeprefix("json").strip()

    parsed = _extract_json(raw_text)
    if parsed is None:
        raise _BatchError(f"ai_filter parse failure: {(resp.text or '')[:200]!r}")

    # GLM-5.1 (and GLM 4.5 Flash before it) often emit a bare list even when the
    # prompt asks for an object. Accept several shapes so we don't silently drop
    # everything on a stylistic difference.
    if isinstance(parsed, dict) and isinstance(parsed.get("evaluations"), list):
        return parsed["evaluations"], resp
    if isinstance(parsed, dict) and isinstance(parsed.get("indices"), list):
        passed = {int(i) for i in parsed["indices"] if str(i).lstrip("-").isdigit()}
    elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed, resp
    elif isinstance(parsed, list):
        passed = {int(i) for i in parsed if str(i).lstrip("-").isdigit()}
    else:
        raise _BatchError(f"ai_filter unexpected JSON structure: {str(parsed)[:200]!r}")

    # Legacy indices-only response: expand into per-listing verdicts.
    return (
        [
            {"index": i, "pass": i in passed, "reason": "(legacy indices-only response)"}
            for i in range(batch_len)
        ],
        resp,
    )


def ai_filter(
    listings: list[Listing],
    profile: Profile,
    display_attrs: list[str] | None = None,
) -> list[Listing]:
    """Filter listings using an LLM to evaluate strict rules.

    Asks the model to return a verdict (pass/fail) and a short reason for every
    listing, persists those verdicts to ``worker/data/filter_logs/<date>.jsonl``
    for inspection, and returns the subset that passed. Listings are split
    into ``_AI_FILTER_BATCH_SIZE`` chunks so the response stays well under the
    model's output token limit; per-batch usage is summed into LAST_RUN_USAGE.
    """
    global LAST_RUN_LOG, LAST_RUN_RAW_RESPONSE, LAST_RUN_USAGE
    LAST_RUN_LOG = []
    LAST_RUN_RAW_RESPONSE = ""
    LAST_RUN_USAGE = None

    if not listings:
        return []

    if os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes"):
        return listings

    system_prompt = _build_system_prompt(profile, display_attrs)

    # Backend selection (ADR-147). Default = Anthropic Haiku 4.5. Earlier
    # revisions tried GLM-5.1 / GLM-4.5-Flash; both failed in prod by emitting
    # chain-of-thought prose into `content` despite "JSON only" — Haiku honors
    # json mode reliably. ``AI_FILTER_BACKEND=local`` instead routes to the home
    # llama-swap box (qwen-coder; fully deterministic at temp=0, ADR-145) behind
    # the polite coordinator. ``chain`` is the ordered fallback: for local it's
    # [primary local, secondary local]; if the box is unavailable it's just Haiku.
    chain = _resolve_filter_chain()
    chain_idx = 0
    provider, model = chain[0]

    n = len(listings)
    batches = [
        list(range(start, min(start + _AI_FILTER_BATCH_SIZE, n)))
        for start in range(0, n, _AI_FILTER_BATCH_SIZE)
    ]
    logger.info(
        f"Calling {provider}/{model} for filtering ({n} listings in "
        f"{len(batches)} batch(es) of up to {_AI_FILTER_BATCH_SIZE})..."
    )

    evaluations_by_index: dict[int, dict[str, Any]] = {}
    raw_responses: list[str] = []
    total_in = 0
    total_out = 0
    # ADR-142: the ~16K-token system block is identical across batches, so
    # batch 1 writes the ephemeral cache (1.25x input) and batches 2..N read it
    # (0.1x input) inside the 5-min TTL. Sum the real per-batch cache counts so
    # the cost panel prices the split honestly (never a hardcoded discount).
    total_cache_read = 0
    total_cache_write = 0

    for batch_no, batch in enumerate(batches, start=1):
        payload_for_llm = []
        for local_i, listing_idx in enumerate(batch):
            lst = listings[listing_idx]
            payload_for_llm.append({
                # Local index — the prompt/response use 0..len(batch); we map
                # back to the global ``listing_idx`` after parsing.
                "index": local_i,
                "title": lst.title,
                "url": lst.url,
                # ADR-131 P0: the model needs the adapter id to apply the
                # serper_shopping single_sku_url exception.
                "source": lst.source,
                "price": lst.unit_price_usd,
                "condition": lst.condition,
                "is_kit": lst.is_kit,
                "kit_module_count": lst.kit_module_count,
                "quantity_available": lst.quantity_available,
                "attrs": lst.attrs,
            })

        # Try the fallback chain in order, starting from the model that last
        # worked (``chain_idx``). On a batch failure (call error, parse failure,
        # bad structure), advance to the next model and stay there for the rest
        # of the run, rather than zeroing the run (ADR-147 — for local that means
        # primary local -> secondary local; Haiku only if the box was unavailable
        # at resolve time, never as an in-run quality fallback).
        err: _BatchError | None = None
        batch_evals = None
        resp = None
        for k in range(chain_idx, len(chain)):
            p, m = chain[k]
            try:
                batch_evals, resp = _call_and_parse_batch(
                    p, m, system_prompt, payload_for_llm, len(batch)
                )
            except _BatchError as be:
                err = be
                if k + 1 < len(chain):
                    _loud(
                        f"filter backend {p}/{m} failed (batch {batch_no}/{len(batches)}): "
                        f"{be.reason} - falling back to {chain[k + 1][1]} for the rest of the run"
                    )
                continue
            chain_idx = k
            provider, model = p, m
            err = None
            break
        if err is not None or resp is None or batch_evals is None:
            # Every model in the chain failed this batch.
            reason = err.reason if err is not None else "no model produced a result"
            _loud(f"Filtering failed (batch {batch_no}/{len(batches)}): {reason}")
            # Operational alert when a LOCAL chain is exhausted, so the owner can
            # investigate (ADR-147). No-ops in dev (push bridge env unset).
            if any(p == "local" for p, _ in chain):
                _notify_filter_failure(profile.slug, reason)
            sentinel = [{
                "index": -1, "pass": False, "reason": f"{reason} (batch {batch_no})",
                "title": "(filter call failed)", "price": None, "url": None, "source": None,
            }]
            _write_filter_log(profile.slug, sentinel)
            LAST_RUN_LOG = sentinel
            LAST_RUN_RAW_RESPONSE = "\n\n--- batch boundary ---\n\n".join(raw_responses)
            LAST_RUN_USAGE = {
                "step": "ai_filter",
                "provider": chain[-1][0],
                "model": chain[-1][1],
                "input_tokens": total_in,
                "output_tokens": total_out,
                "cache_read_input_tokens": total_cache_read,
                "cache_creation_input_tokens": total_cache_write,
            }
            return []

        raw_responses.append(resp.text or "")
        total_in += resp.input_tokens or 0
        total_out += resp.output_tokens or 0
        total_cache_read += resp.cache_read_input_tokens or 0
        total_cache_write += resp.cache_creation_input_tokens or 0

        # Map local indices back to global indices and merge.
        for ev in batch_evals:
            if not isinstance(ev, dict):
                continue
            idx_raw = ev.get("index")
            if idx_raw is None:
                continue
            try:
                local_i = int(idx_raw)
            except (TypeError, ValueError):
                continue
            if not (0 <= local_i < len(batch)):
                continue
            global_idx = batch[local_i]
            if global_idx in evaluations_by_index:
                continue
            evaluations_by_index[global_idx] = {
                "pass": bool(ev.get("pass")),
                "reason": str(ev.get("reason", "")).strip(),
                "extracted_features": ev.get("extracted_features", {}),
            }

    LAST_RUN_RAW_RESPONSE = "\n\n--- batch boundary ---\n\n".join(raw_responses)
    LAST_RUN_USAGE = {
        "step": "ai_filter",
        "provider": provider,
        "model": model,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cache_read_input_tokens": total_cache_read,
        "cache_creation_input_tokens": total_cache_write,
    }

    # Build log entries (one per listing the model evaluated) and pick the survivors.
    log_entries: list[dict[str, Any]] = []
    passed_listings: list[Listing] = []

    for idx in range(len(listings)):
        lst = listings[idx]
        verdict = evaluations_by_index.get(idx)
        # ADR-109: carry the per-source search URL so rejection attribution can
        # distinguish two `universal_ai_search` rows (which share `lst.source`)
        # by the URL the listing was fetched from. cli.py sets this attr.
        source_url = (lst.attrs or {}).get("source_url")
        if verdict is None:
            log_entries.append({
                "index": idx,
                "pass": False,
                "reason": "no verdict returned by model",
                "title": lst.title,
                "price": lst.unit_price_usd,
                "url": lst.url,
                "source": lst.source,
                "source_url": source_url,
            })
            continue

        passed = verdict["pass"]
        reason = verdict["reason"] or ("passed all rules" if passed else "no reason given")
        log_entries.append({
            "index": idx,
            "pass": passed,
            "reason": reason,
            "title": lst.title,
            "price": lst.unit_price_usd,
            "url": lst.url,
            "source": lst.source,
            "source_url": source_url,
        })
        if passed:
            extracted = verdict.get("extracted_features", {})
            if extracted and isinstance(extracted, dict):
                if lst.attrs is None:
                    lst.attrs = {}
                for k, v in extracted.items():
                    if not v or not str(v).strip():
                        continue
                    # Structured data wins — don't override real API fields
                    # with title-derived guesses.
                    if k == "condition" and lst.condition:
                        continue
                    if k == "brand" and lst.brand:
                        continue
                    if k == "quantity" and lst.quantity_available is not None:
                        continue
                    if k not in lst.attrs:
                        lst.attrs[k] = str(v).strip()
            passed_listings.append(lst)

    _write_filter_log(profile.slug, log_entries)
    LAST_RUN_LOG = list(log_entries)
    logger.info(f"LLM kept {len(passed_listings)} out of {len(listings)} listings.")

    return passed_listings
