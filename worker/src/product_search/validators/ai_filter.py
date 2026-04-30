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


def _filter_log_path() -> Path:
    """Return today's filter-log file under ``worker/data/filter_logs/``."""
    # worker/src/product_search/validators/ai_filter.py -> worker/
    worker_dir = Path(__file__).resolve().parent.parent.parent.parent
    log_dir = worker_dir / "data" / "filter_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{datetime.now(tz=UTC).date().isoformat()}.jsonl"


def _write_filter_log(slug: str, entries: list[dict]) -> None:
    try:
        path = _filter_log_path()
        timestamp = datetime.now(tz=UTC).isoformat()
        with path.open("a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps({"timestamp": timestamp, "product": slug, **entry}) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write filter log: {e}")


def ai_filter(listings: list[Listing], profile: Profile) -> list[Listing]:
    """Filter listings using an LLM to evaluate strict rules.

    Asks the model to return a verdict (pass/fail) and a short reason for every
    listing, persists those verdicts to ``worker/data/filter_logs/<date>.jsonl``
    for inspection, and returns the subset that passed.
    """
    if not listings:
        return []

    if os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes"):
        return listings

    rules = [r.rule for r in profile.spec_filters]
    target_desc = f"Target: {profile.target.amount} {profile.target.unit}"
    if profile.target.configurations:
        target_desc += f" (Need exactly one of these configs: {[c.model_dump() for c in profile.target.configurations]})"

    payload_for_llm = []
    for i, lst in enumerate(listings):
        payload_for_llm.append({
            "index": i,
            "title": lst.title,
            "price": lst.unit_price_usd,
            "condition": lst.condition,
            "is_kit": lst.is_kit,
            "kit_module_count": lst.kit_module_count,
            "attrs": lst.attrs,
        })

    system_prompt = f"""You are a product filter.
The user wants: {profile.display_name}
Description: {profile.description}
{target_desc}

Rules to apply:
{json.dumps(rules, indent=2)}

How to decide each listing's verdict:
- For every rule, look at BOTH the listing's `attrs` dict AND its `title`. The title
  typically encodes attributes that aren't in attrs (e.g. "RDIMM ECC DDR5-4800 32GB"
  implies form_factor=RDIMM, ecc=true, speed_mts=4800, capacity_gb=32).
- "pass": true means NO rule is clearly violated.
- Unknown is NOT the same as "failed". If a rule references an attribute that is
  missing from attrs AND not contradicted by the title, treat the rule as passed.
  Only reject when an attribute is PRESENT and clearly violates the rule, or the
  TITLE clearly contradicts it (e.g. "UDIMM" in the title for a form_factor: RDIMM rule).
- title_excludes rules: fail only if one of the excluded substrings appears in the
  title (case-insensitive). Otherwise pass.

You will receive a JSON list of products. Output a JSON object with a single key
"evaluations" containing an array with one entry PER PRODUCT, in input order. Each
entry must have these exact keys:
  - "index": integer, matching the input index
  - "pass": boolean
  - "reason": short string (1 sentence) — for failures, name the specific rule that
    failed and quote the offending word from attrs or title.

Every input product must appear exactly once in "evaluations". Do not omit any.
IMPORTANT: Do NOT output any chain-of-thought or reasoning text outside the JSON.
ONLY output the JSON object.
"""

    # Use GLM 4.5 Flash, NOT GLM-5.1. GLM-5.1 is a reasoning model that ignores
    # response_format=json_object and dumps chain-of-thought prose into `content`
    # even when explicitly told not to. GLM 4.5 Flash honors json_object mode and
    # was the Phase 5 benchmark winner (10/10) for compact structured output —
    # exactly what filtering needs. It also costs ~10x less.
    logger.info("Calling GLM 4.5 Flash for filtering...")
    try:
        resp = call_llm(
            provider="glm",
            model="glm-4.5-flash",
            system=system_prompt,
            messages=[Message(role="user", content=json.dumps(payload_for_llm, indent=2))],
            max_tokens=8192,
            response_format="json",
        )

        raw_text = resp.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()
        raw_text = raw_text.removeprefix("json").strip()

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            _loud(f"JSON parse failed. Raw response (first 1000 chars):\n{resp.text[:1000]}")
            _write_filter_log(profile.slug, [{
                "index": -1, "pass": False,
                "reason": f"ai_filter parse failure: {resp.text[:200]!r}",
                "title": "(filter call failed)", "price": None, "url": None, "source": None,
            }])
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
            _write_filter_log(profile.slug, [{
                "index": -1, "pass": False,
                "reason": f"ai_filter unexpected JSON structure: {str(parsed)[:200]!r}",
                "title": "(filter call failed)", "price": None, "url": None, "source": None,
            }])
            return []

        if bare_indices is not None:
            passed_set = set(bare_indices)
            evaluations = [
                {"index": i, "pass": i in passed_set, "reason": "(legacy indices-only response)"}
                for i in range(len(listings))
            ]

    except Exception as e:
        _loud(f"Filtering LLM call failed: {e!r}")
        _write_filter_log(profile.slug, [{
            "index": -1, "pass": False, "reason": f"ai_filter exception: {e!r}",
            "title": "(filter call failed)", "price": None, "url": None, "source": None,
        }])
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
    logger.info(f"LLM kept {len(passed_listings)} out of {len(listings)} listings.")

    return passed_listings
