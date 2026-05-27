
import re

with open(r"c:\Users\ariro\OneDrive\Personal\Product search\worker\src\product_search\cli.py", "r", encoding="utf-8") as f:
    content = f.read()

pattern = r"(    for source in profile\.sources:.*?        \}\)\n)"
match = re.search(pattern, content, re.DOTALL)
if not match:
    print("Pattern not found")
else:
    original = match.group(1)
    
    new_code = """    from concurrent.futures import ThreadPoolExecutor

    def _process_source(source) -> tuple[list[Listing], dict[str, Any], list[dict[str, Any]]]:
        query = AdapterQuery.from_profile_source(source.model_dump())
        listings: list[Listing] = []
        error_msg: str | None = None
        # ADR-084: extra per-source signal for the source-reason classifier.
        skip_reason: str | None = None
        diagnostics: dict[str, Any] | None = None
        usages: list[dict[str, Any]] = []

        try:
            if source.id == "ebay_search":
                from product_search.adapters.ebay import EbayAuthError
                from product_search.adapters.ebay import fetch as fetch_ebay
                try:
                    listings = fetch_ebay(query)
                except EbayAuthError as exc:
                    print(f"ERROR (eBay auth): {exc}", file=sys.stderr)
                    print(
                        "Tip: set WORKER_USE_FIXTURES=1 to use saved fixtures "
                        "while waiting for eBay API credentials.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            elif source.id == "nemixram_storefront":
                from product_search.adapters.nemixram import fetch as fetch_nemixram
                listings = fetch_nemixram(query)
            elif source.id == "cloudstoragecorp_ebay":
                from product_search.adapters.cloudstoragecorp import fetch as fetch_cloud
                listings = fetch_cloud(query)
            elif source.id == "memstore_ebay":
                from product_search.adapters.memstore import fetch as fetch_memstore
                listings = fetch_memstore(query)
            elif source.id == "universal_ai_search":
                from product_search.adapters import universal_ai as universal_ai_mod
                listings = universal_ai_mod.fetch(query, profile=profile)
                src_url_for_attrs = query.extra.get("url") or query.storefront_url
                if src_url_for_attrs:
                    for _lst in listings:
                        if _lst.attrs is None:
                            _lst.attrs = {}
                        _lst.attrs["source_url"] = src_url_for_attrs
                
                # Fetch thread-local variables safely
                tls_skip_reason = getattr(universal_ai_mod.tls, "last_skip_reason", None)
                if tls_skip_reason:
                    from product_search.source_reasons import WATCH_GATE_REASON_PREFIX
                    skip_reason = tls_skip_reason
                    if not skip_reason.startswith(WATCH_GATE_REASON_PREFIX):
                        error_msg = skip_reason
                
                tls_diagnostics = getattr(universal_ai_mod.tls, "last_fetch_diagnostics", None)
                if tls_diagnostics:
                    diagnostics = dict(tls_diagnostics)
                
                tls_usage = getattr(universal_ai_mod.tls, "last_run_usage", None)
                if tls_usage:
                    usage = dict(tls_usage)
                    src_url = (query.extra.get("url") or query.storefront_url or "?")
                    from urllib.parse import urlparse as _urlparse
                    host_for_step = _urlparse(src_url).netloc.lower()
                    if host_for_step.startswith("www."):
                        host_for_step = host_for_step[4:]
                    usage["step"] = host_for_step or src_url
                    usages.append(usage)
            else:
                error_msg = "no adapter wired"
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            print(f"ERROR ({source.id} fetch): {exc}", file=sys.stderr)
            
        display_source = source.id
        match_host: str | None = None
        match_url: str | None = None
        if source.id == "universal_ai_search":
            src_url = query.extra.get("url") or query.storefront_url
            if src_url:
                from urllib.parse import urlparse
                host = urlparse(src_url).netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    match_host = host
                    display_source = host
                match_url = src_url

        stat = {
            "source": source.id,
            "match_host": match_host,
            "match_url": match_url,
            "display_source": display_source,
            "fetched": len(listings),
            "error": error_msg,
            "skip_reason": skip_reason,
            "diagnostics": diagnostics,
        }
        return listings, stat, usages

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_process_source, source) for source in profile.sources]
        for future in futures:
            try:
                listings, stat, usages = future.result()
                all_listings.extend(listings)
                source_stats.append(stat)
                universal_ai_usage.extend(usages)
            except Exception as exc:
                print(f"ERROR (worker thread): {exc}", file=sys.stderr)
"""
    content = content.replace(original, new_code)
    with open(r"c:\Users\ariro\OneDrive\Personal\Product search\worker\src\product_search\cli.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Replaced successfully")

