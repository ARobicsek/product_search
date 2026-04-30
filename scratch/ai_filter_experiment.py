"""AI-Aided Filtering Experiment.

This script replaces `run_pipeline` with a GLM-5.1 call that returns
the indices of listings that pass the strict rules.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add worker/src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker" / "src"))

from product_search.profile import load_profile
from product_search.models import AdapterQuery, Listing
from product_search.llm import call_llm, Message
from product_search.config import synth_config
from product_search.synthesizer import synthesize, write_report

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def ai_filter(listings: list[Listing], profile) -> list[Listing]:
    if not listings:
        return []

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
            raise ValueError("LLM did not return a list")
    except Exception as e:
        logger.error(f"Filtering LLM failed: {e}")
        return []

    logger.info(f"LLM kept {len(passed_indices)} out of {len(listings)} listings.")
    
    passed_listings = []
    for idx in passed_indices:
        if 0 <= int(idx) < len(listings):
            passed_listings.append(listings[int(idx)])

    return passed_listings

def run_experiment(slug: str, use_universal_ai: bool = False):
    try:
        profile = load_profile(slug)
    except Exception as e:
        logger.error(f"Failed to load profile {slug}: {e}")
        return

    all_listings = []

    # Let's mock the fetching by loading fixtures if possible, or using deterministic adapters
    # For DDR5, we can use the deterministic adapters with WORKER_USE_FIXTURES=1
    import os
    os.environ["WORKER_USE_FIXTURES"] = "1"

    if use_universal_ai:
        from scratch.universal_ai_adapter import fetch as fetch_universal
        for source in profile.sources:
            if source.id == "universal_ai_search":
                q = AdapterQuery.from_profile_source(source.model_dump())
                logger.info("Running universal_ai_search adapter...")
                listings = fetch_universal(q)
                all_listings.extend(listings)
    else:
        # Use existing Phase 2/6 deterministic adapters
        from product_search.adapters.ebay import fetch as fetch_ebay
        from product_search.adapters.nemixram import fetch as fetch_nemixram
        
        for source in profile.sources:
            q = AdapterQuery.from_profile_source(source.model_dump())
            if source.id == "ebay_search":
                try:
                    all_listings.extend(fetch_ebay(q))
                except Exception as e:
                    logger.error(f"eBay adapter failed: {e}")
            elif source.id == "nemixram_storefront":
                all_listings.extend(fetch_nemixram(q))

    logger.info(f"Fetched {len(all_listings)} total raw listings.")

    # Apply AI Filter
    passed = ai_filter(all_listings, profile)

    # Calculate total_for_target_usd (this is a simple deterministic math step)
    from product_search.validators.pipeline import _calculate_total
    for lst in passed:
        lst.total_for_target_usd = _calculate_total(lst, profile)

    # Synthesize
    if passed:
        cfg = synth_config()
        logger.info(f"Synthesizing {len(passed)} listings using {cfg.provider}/{cfg.model}")
        try:
            result = synthesize(
                passed,
                None,
                profile,
                provider=cfg.provider,
                model=cfg.model,
                snapshot_date=datetime.now(tz=UTC).date()
            )
            print("\n" + "="*80)
            print("SYNTHESIZED REPORT:")
            print("="*80)
            print(result.report_md)
        except Exception as e:
            logger.error(f"Synthesizer failed: {e}")
    else:
        logger.info("No listings passed the AI filter.")

if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "ddr5-rdimm-256gb"
    use_ai_adapter = "--ai-adapter" in sys.argv
    run_experiment(slug, use_universal_ai=use_ai_adapter)
