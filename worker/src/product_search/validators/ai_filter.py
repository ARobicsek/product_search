"""AI-Aided Filtering for the validation pipeline."""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from product_search.llm import Message, call_llm
from product_search.models import Listing
from product_search.profile import Profile

logger = logging.getLogger(__name__)


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

    system_prompt = f"""You are a strict product filter.
The user wants: {profile.display_name}
Description: {profile.description}
{target_desc}

Strict Rules to Apply:
{json.dumps(rules, indent=2)}

You will receive a JSON list of products.
Output a JSON object with a single key "evaluations" containing an array with one entry PER PRODUCT in the input list, in the same order. Each entry must be an object with these exact keys:
  - "index": integer, matching the input index
  - "pass": boolean — true only if the product strictly satisfies ALL rules
  - "reason": short string (1 sentence) explaining the decision; for failures, name the specific rule that failed

Every input product must appear exactly once in "evaluations". Do not omit any.
IMPORTANT: Do NOT output any chain-of-thought or reasoning text outside the JSON. ONLY output the JSON object.
"""

    logger.info("Calling GLM-5.1 for filtering...")
    try:
        resp = call_llm(
            provider="glm",
            model="glm-5.1",
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
            logger.error(f"Failed to parse JSON. Raw LLM output:\n{resp.text}")
            return []

        evaluations: list[dict] = []
        if isinstance(parsed, dict) and "evaluations" in parsed and isinstance(parsed["evaluations"], list):
            evaluations = parsed["evaluations"]
        elif isinstance(parsed, dict) and "indices" in parsed and isinstance(parsed["indices"], list):
            # Backwards-compat: older prompt shape returned only the passing indices.
            passed_set = {int(i) for i in parsed["indices"] if isinstance(i, int | str) and str(i).lstrip("-").isdigit()}
            evaluations = [
                {"index": i, "pass": i in passed_set, "reason": "(legacy indices-only response)"}
                for i in range(len(listings))
            ]
        else:
            logger.error(f"LLM returned unexpected JSON structure: {parsed}")
            return []

    except Exception as e:
        logger.error(f"Filtering LLM failed: {e}")
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
