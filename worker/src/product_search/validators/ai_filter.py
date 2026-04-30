"""AI-Aided Filtering for the validation pipeline."""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from product_search.llm import Message, call_llm
from product_search.models import Listing
from product_search.profile import Profile

logger = logging.getLogger(__name__)


def _loud(msg: str) -> None:
    """Print to stderr so the message is visible in GitHub Actions logs.

    `logger.error` requires the action's log-level config to flush to stderr;
    a bare print is more reliable for prod-failure visibility.
    """
    print(f"[ai_filter] {msg}", file=sys.stderr, flush=True)


def _extract_json(text: str) -> object | None:
    """Return the first valid JSON object/array embedded in ``text``, else None.

    First tries to parse the whole string. If that fails, walks from the first
    ``{`` or ``[`` and uses ``json.JSONDecoder.raw_decode`` to find the longest
    valid JSON value at that position. Tolerates models that prepend a prose
    preamble like "Let me analyze the products..." before the structured JSON
    (observed with GLM-4.5-Flash on 2026-04-30 even with response_format=json).
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        return obj
    return None


def _filter_log_path() -> Path:
    """Return today's filter-log file under ``worker/data/filter_logs/``."""
    # worker/src/product_search/validators/ai_filter.py -> worker/
    worker_dir = Path(__file__).resolve().parent.parent.parent.parent
    log_dir = worker_dir / "data" / "filter_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{datetime.now(tz=UTC).date().isoformat()}.jsonl"


def _repo_reports_dir() -> Path | None:
    """Return ``<repo>/reports`` if discoverable from this file's location.

    Used to drop a per-run filter log alongside the committed daily report so
    the diagnostic survives even when GH Actions artifact downloads require
    auth that the operator may not have. Returns None when the repo layout
    can't be located (tests / unusual CWDs).
    """
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
LAST_RUN_LOG: list[dict] = []
LAST_RUN_RAW_RESPONSE: str = ""


def _write_filter_log(slug: str, entries: list[dict]) -> None:
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


def ai_filter(listings: list[Listing], profile: Profile) -> list[Listing]:
    """Filter listings using an LLM to evaluate strict rules.

    Asks the model to return a verdict (pass/fail) and a short reason for every
    listing, persists those verdicts to ``worker/data/filter_logs/<date>.jsonl``
    for inspection, and returns the subset that passed.
    """
    global LAST_RUN_LOG, LAST_RUN_RAW_RESPONSE
    LAST_RUN_LOG = []
    LAST_RUN_RAW_RESPONSE = ""

    if not listings:
        return []

    if os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes"):
        return listings

    # Dump FULL rule definitions (rule type + values/value/etc.) — not just the
    # rule names. Earlier revisions stripped extras and sent only `r.rule`, which
    # left the LLM guessing what `form_factor_in` allowed, what `voltage_eq`
    # required, and which substrings `title_excludes` named. That guess
    # collapsed to "reject everything" in prod.
    rules_full = [r.model_dump() for r in profile.spec_filters]
    target_desc = f"Target: {profile.target.amount} {profile.target.unit}"
    if profile.target.configurations:
        target_desc += f" (Need exactly one of these configs: {[c.model_dump() for c in profile.target.configurations]})"

    payload_for_llm = []
    for i, lst in enumerate(listings):
        payload_for_llm.append({
            "index": i,
            "title": lst.title,
            "url": lst.url,
            "price": lst.unit_price_usd,
            "condition": lst.condition,
            "is_kit": lst.is_kit,
            "kit_module_count": lst.kit_module_count,
            "quantity_available": lst.quantity_available,
            "attrs": lst.attrs,
        })

    system_prompt = f"""You are a product filter.
The user wants: {profile.display_name}
Description: {profile.description}
{target_desc}

Rules to apply (each rule is a dict with a "rule" type and its parameters):
{json.dumps(rules_full, indent=2)}

How each rule type works (only the ones present above apply):
- form_factor_in {{values:[...]}}: pass if attrs.form_factor is in values, OR if neither
  attrs.form_factor nor the title indicates a specific form factor. Reject only when
  attrs.form_factor is set to something not in values, OR the title clearly contains a
  different form factor (e.g. "UDIMM" / "SODIMM" / "LRDIMM" in the title when the values
  list does not include that form factor).
- speed_mts_min {{value:N}}: pass if attrs.speed_mts >= N, or if speed is unknown.
  Reject only if attrs.speed_mts is set and below N, OR the title clearly states a
  lower speed (e.g. "DDR5-4400" or "PC5-32000" when min is 4800).
- ecc_required: pass if attrs.ecc is true OR ecc is unknown. Reject only if attrs.ecc
  is explicitly false, OR the title clearly says "non-ECC".
- voltage_eq {{value:V}}: pass if attrs.voltage_v equals V, OR voltage is unknown
  (which it almost always is — voltage is rarely in titles). Only reject when
  attrs.voltage_v is set and clearly != V.
- min_quantity_for_target: pass unless the listing definitely cannot hit the target.
  Compare attrs.capacity_gb against the target configurations above. If
  attrs.capacity_gb does not match any config's module_capacity_gb, reject. If
  capacity is unknown, pass. If quantity_available is known and (quantity_available *
  kit_module_count) is less than the required module_count, reject.
- in_stock: pass unless quantity_available is explicitly 0 or negative. Pass when
  quantity_available is null/unknown.
- single_sku_url: reject only if the URL clearly points at a search results page
  (e.g. contains "/sch/", "search?", or "?_nkw="). Otherwise pass.
- title_excludes {{values:[...]}}: reject if any string in values appears in the
  title (case-insensitive substring match). Otherwise pass.

Decision rules:
- "pass": true means NO rule above is clearly violated.
- Unknown is NOT the same as failed. Apply each rule to the data you actually have
  (attrs, title, url, quantity_available). If a rule depends on an attribute that
  isn't present and isn't implied by the title, treat that rule as passed.
- The title is informative: "RDIMM ECC DDR5-4800 32GB" implies form_factor=RDIMM,
  ecc=true, speed_mts=4800, capacity_gb=32. Use those implications when applying
  rules.
- For each failure, name the specific rule and quote the offending substring from
  attrs/title/url so the human reviewer can verify.

You will receive a JSON list of products. Output a JSON object with a single key
"evaluations" containing an array with one entry PER PRODUCT, in input order. Each
entry must have these exact keys:
  - "index": integer, matching the input index
  - "pass": boolean
  - "reason": short string (1 sentence) — for failures, name the specific rule that
    failed and quote the offending word from attrs/title/url.

Every input product must appear exactly once in "evaluations". Do not omit any.
IMPORTANT: Do NOT output any chain-of-thought or reasoning text outside the JSON.
ONLY output the JSON object.
"""

    # Use Anthropic Claude Haiku 4.5. Earlier revisions tried GLM-5.1 (a
    # reasoning model that ignores response_format=json_object) and then
    # GLM-4.5-Flash; both failed in prod by emitting chain-of-thought prose
    # into `content` despite explicit "JSON only" instructions. The
    # 2026-04-30 run after committing the diagnostic block confirmed
    # GLM-4.5-Flash also dumps prose like "Let me analyze the products one
    # by one according to the rules provided. First, let's review the
    # rules: 1. form_factor_in {values:..."  — JSON parse fails on the
    # first character. Haiku 4.5 honors json mode reliably (it's already
    # the synth model per ADR-019). Cost is fine — ~$0.005/run for ~100
    # listings vs essentially free for GLM, but correctness > cost here.
    logger.info("Calling Claude Haiku 4.5 for filtering...")
    try:
        resp = call_llm(
            provider="anthropic",
            model="claude-haiku-4-5",
            system=system_prompt,
            messages=[Message(role="user", content=json.dumps(payload_for_llm, indent=2))],
            max_tokens=8192,
            response_format="json",
        )
        LAST_RUN_RAW_RESPONSE = resp.text or ""

        raw_text = resp.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()
        raw_text = raw_text.removeprefix("json").strip()

        parsed = _extract_json(raw_text)
        if parsed is None:
            _loud(f"JSON parse failed. Raw response (first 1000 chars):\n{resp.text[:1000]}")
            sentinel = [{
                "index": -1, "pass": False,
                "reason": f"ai_filter parse failure: {resp.text[:200]!r}",
                "title": "(filter call failed)", "price": None, "url": None, "source": None,
            }]
            _write_filter_log(profile.slug, sentinel)
            LAST_RUN_LOG = sentinel
            return []

        # GLM-5.1 (and GLM 4.5 Flash before it) often emit a bare list even when the
        # prompt asks for an object. Accept several shapes so we don't silently drop
        # everything on a stylistic difference. Documented post-mortem: yesterday's
        # local trace showed GLM returning `[0]` for an `{"indices": [...]}` prompt.
        evaluations: list[dict] = []
        bare_indices: list[int] | None = None

        if isinstance(parsed, dict) and isinstance(parsed.get("evaluations"), list):
            evaluations = parsed["evaluations"]
        elif isinstance(parsed, dict) and isinstance(parsed.get("indices"), list):
            bare_indices = [int(i) for i in parsed["indices"] if str(i).lstrip("-").isdigit()]
        elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            # Bare array of evaluation objects.
            evaluations = parsed
        elif isinstance(parsed, list):
            # Bare array of integers (legacy indices-only).
            bare_indices = [int(i) for i in parsed if str(i).lstrip("-").isdigit()]
        else:
            _loud(f"Unexpected JSON structure: {str(parsed)[:500]}")
            sentinel = [{
                "index": -1, "pass": False,
                "reason": f"ai_filter unexpected JSON structure: {str(parsed)[:200]!r}",
                "title": "(filter call failed)", "price": None, "url": None, "source": None,
            }]
            _write_filter_log(profile.slug, sentinel)
            LAST_RUN_LOG = sentinel
            return []

        if bare_indices is not None:
            passed_set = set(bare_indices)
            evaluations = [
                {"index": i, "pass": i in passed_set, "reason": "(legacy indices-only response)"}
                for i in range(len(listings))
            ]

    except Exception as e:
        _loud(f"Filtering LLM call failed: {e!r}")
        sentinel = [{
            "index": -1, "pass": False, "reason": f"ai_filter exception: {e!r}",
            "title": "(filter call failed)", "price": None, "url": None, "source": None,
        }]
        _write_filter_log(profile.slug, sentinel)
        LAST_RUN_LOG = sentinel
        return []

    # Build log entries (one per listing the model evaluated) and pick the survivors.
    log_entries: list[dict] = []
    passed_listings: list[Listing] = []
    seen_indices: set[int] = set()

    for ev in evaluations:
        if not isinstance(ev, dict):
            continue
        try:
            idx = int(ev.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(listings)) or idx in seen_indices:
            continue
        seen_indices.add(idx)

        passed = bool(ev.get("pass"))
        reason = str(ev.get("reason", "")).strip() or ("passed all rules" if passed else "no reason given")
        lst = listings[idx]
        log_entries.append({
            "index": idx,
            "pass": passed,
            "reason": reason,
            "title": lst.title,
            "price": lst.unit_price_usd,
            "url": lst.url,
            "source": lst.source,
        })
        if passed:
            passed_listings.append(lst)

    # Mark any listings the model dropped from its response as failures with an explicit reason
    # so the log is exhaustive and the user can see exactly what was evaluated.
    for idx in range(len(listings)):
        if idx in seen_indices:
            continue
        lst = listings[idx]
        log_entries.append({
            "index": idx,
            "pass": False,
            "reason": "no verdict returned by model",
            "title": lst.title,
            "price": lst.unit_price_usd,
            "url": lst.url,
            "source": lst.source,
        })

    _write_filter_log(profile.slug, log_entries)
    LAST_RUN_LOG = list(log_entries)
    logger.info(f"LLM kept {len(passed_listings)} out of {len(listings)} listings.")

    return passed_listings
