"""Universal AI Adapter for extracting listings from arbitrary HTML."""

import json
import logging
from datetime import UTC, datetime

import httpx

from product_search.models import AdapterQuery, Listing
from product_search.llm import call_llm, Message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Universal Web Scraper.
The user will provide you with the raw text of a product search results page from an arbitrary vendor.
Your task is to extract every relevant product listing into a JSON array.

For each listing, extract:
- title (string)
- price_usd (float, numeric only)
- url (string, must be the exact partial or absolute URL found in the text)
- condition (string, guess "new", "used", or "refurbished" based on text)

Respond ONLY with a valid JSON array of objects. Do not wrap in markdown blocks, just the raw JSON text.
If no products are found, return [].

CRITICAL: Do NOT hallucinate URLs. The URL you output MUST be present in the raw text exactly as you output it.
"""

def fetch(query: AdapterQuery) -> list[Listing]:
    url = query.extra.get("url")
    if not url:
        logger.warning("No 'url' provided in query for universal_ai_search.")
        return []

    # 1. Fetch raw page text
    logger.info(f"Fetching {url}...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    with httpx.Client(follow_redirects=True, headers=headers) as client:
        try:
            resp = client.get(url, timeout=15.0)
            resp.raise_for_status()
            text_content = resp.text
        except Exception as e:
            logger.error(f"Failed to fetch URL: {e}")
            return []

    # Strip some bloat to save tokens
    try:
        from selectolax.parser import HTMLParser
        tree = HTMLParser(text_content)
        # Remove script and style tags
        for tag in tree.css("script, style, svg, nav, footer"):
            tag.decompose()
        clean_text = tree.body.text(separator=" ", strip=True) if tree.body else text_content
    except ImportError:
        clean_text = text_content
    
    # Truncate text if it's too huge
    if len(clean_text) > 40000:
        clean_text = clean_text[:40000]

    logger.info(f"Page fetched. Length: {len(clean_text)} chars. Calling GLM-5.1...")

    # 2. Extract with LLM
    try:
        llm_resp = call_llm(
            provider="glm",
            model="glm-5.1",
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=clean_text)],
            max_tokens=4096
        )
    except Exception as e:
        logger.error(f"LLM extraction failed: {e}")
        return []
    
    # 3. Parse JSON response
    raw_text = llm_resp.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()
    raw_text = raw_text.removeprefix("json").strip()
    
    try:
        raw_items = json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON: {e}\nRaw output: {llm_resp.text}")
        return []

    if not isinstance(raw_items, list):
        logger.error("LLM did not return a list")
        return []

    # 4. Filter and convert to Listing objects
    results: list[Listing] = []
    fetched_at = datetime.now(tz=UTC)
    
    for item in raw_items:
        if not isinstance(item, dict):
            continue
            
        ext_url = item.get("url", "")
        # Safety Check: Did the LLM hallucinate the URL?
        # We check if the exact string exists in the raw HTML
        if ext_url and ext_url not in text_content:
            logger.warning(f"Discarding hallucinated URL: {ext_url}")
            continue
            
        # Normalize relative URLs
        if ext_url.startswith("/"):
            # Very basic base URL inference
            base = "/".join(url.split("/")[:3])
            ext_url = base + ext_url
            
        if not ext_url or not item.get("title"):
            continue

        try:
            unit_price_usd = float(item.get("price_usd", 0.0))
        except (ValueError, TypeError):
            unit_price_usd = 0.0

        lst = Listing(
            source="universal_ai_search",
            url=ext_url,
            title=item.get("title"),
            fetched_at=fetched_at,
            brand=None,
            mpn=None,
            attrs={},
            condition=item.get("condition", "new").lower(),
            is_kit=False,
            kit_module_count=1,
            unit_price_usd=unit_price_usd,
            kit_price_usd=None,
            quantity_available=None, 
            seller_name="ExtractedVendor",
            seller_rating_pct=None,
            seller_feedback_count=None,
            ship_from_country=None
        )
        results.append(lst)

    logger.info(f"Extracted {len(results)} valid listings.")
    return results
