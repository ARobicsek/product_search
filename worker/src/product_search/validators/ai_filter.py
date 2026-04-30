"""AI-Aided Filtering for the validation pipeline."""

import json
import logging
from product_search.models import Listing
from product_search.profile import Profile
from product_search.llm import call_llm, Message

logger = logging.getLogger(__name__)

def ai_filter(listings: list[Listing], profile: Profile) -> list[Listing]:
    """Filter listings using an LLM to evaluate strict rules."""
    if not listings:
        return []

    import os
    if os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes"):
        return listings

    # 1. Build context
    rules = [r.rule for r in profile.spec_filters]
    target_desc = f"Target: {profile.target.amount} {profile.target.unit}"
    if profile.target.configurations:
        target_desc += f" (Need exactly one of these configs: {[c.model_dump() for c in profile.target.configurations]})"

    # 2. Build minimal payload to send to LLM to save tokens and avoid URL hallucination
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
Output a JSON array of INTEGER INDICES of the products that STRICTLY PASS all rules.
Do not include products that fail the rules. If none pass, output [].
IMPORTANT: Do NOT output any chain-of-thought or reasoning text. ONLY output the JSON array.
"""

    logger.info("Calling GLM-5.1 for filtering...")
    try:
        resp = call_llm(
            provider="glm",
            model="glm-5.1",
            system=system_prompt,
            messages=[Message(role="user", content=json.dumps(payload_for_llm, indent=2))],
            max_tokens=4096,
            response_format="json"
        )
        
        raw_text = resp.text.strip()
        # Clean up possible markdown wrappers
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()
        raw_text = raw_text.removeprefix("json").strip()
        
        try:
            passed_indices = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON. Raw LLM output:\n{resp.text}")
            return []
            
        if not isinstance(passed_indices, list):
            logger.error("LLM did not return a list")
            return []
            
    except Exception as e:
        logger.error(f"Filtering LLM failed: {e}")
        return []

    logger.info(f"LLM kept {len(passed_indices)} out of {len(listings)} listings.")
    
    passed_listings = []
    for idx in passed_indices:
        try:
            idx = int(idx)
            if 0 <= idx < len(listings):
                passed_listings.append(listings[idx])
        except (ValueError, TypeError):
            continue

    return passed_listings
