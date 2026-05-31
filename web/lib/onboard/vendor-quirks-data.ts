// FROZEN ARTIFACT (Phase 36). Was generated from worker/.../vendor_quirks.yaml,
// which was retired with the self-scraping tier; the generator (sync-prompt.cjs)
// is gone. Kept as static data for the legacy v1 validation layer + the
// search-URL parity guard. Edit directly if ever needed.

// Hosts (www-stripped) whose single-SKU products must carry BOTH a search URL
// and a page_type:"detail" URL — ADR-067 force_detail_backup enforcement.
export const FORCE_DETAIL_BACKUP_HOSTS: ReadonlySet<string> = new Set(["adorama.com","amazon.com","bestbuy.com","costco.com","crutchfield.com","gamestop.com","homedepot.com","lowes.com","macys.com","newegg.com","rei.com","sweetwater.com","target.com","walmart.com","williams-sonoma.com"]);

// Hosts (www-stripped) that AlterLab renders fine in production even when a
// bare datacenter fetch gets a 5xx / tiny body. Used by probe-url.ts to avoid
// false-negative demotions.
export const ALTERLAB_KNOWN_GOOD_HOSTS: ReadonlySet<string> = new Set(["amazon.com","bestbuy.com","ebay.com","target.com","walmart.com","williams-sonoma.com"]);

// Hosts (www-stripped) whose search-tile walker is blind, so the onboarder is
// told to PREFER page_type:"detail" URLs. ADR-079: a transient probe failure on
// such a vendor must NOT demote its detail URL — the runtime escalation ladder +
// circuit breaker (ADR-071/078) own retry; the registry says this vendor needs
// the detail URL, so the gate keeps it in `sources` with an advisory note.
export const PREFER_DETAIL_HOSTS: ReadonlySet<string> = new Set([]);

// ADR-105: canonical per-vendor search-results URL templates. `{q}` is the
// URL-encoded keyword placeholder. renderSearchUrl() (search-url-shared.ts)
// fills it deterministically so the onboarder never guesses a vendor's search
// param name. Parity-checked against the worker's render_search_url().
export const SEARCH_URL_TEMPLATES: Readonly<Record<string, string>> = {
  "abebooks.com": "https://www.abebooks.com/servlet/SearchResults?kn={q}&sortby=17",
  "alibris.com": "https://www.alibris.com/booksearch?keyword={q}",
  "amazon.com": "https://www.amazon.com/s?k={q}",
  "backmarket.com": "https://www.backmarket.com/en-us/search?q={q}",
  "betterworldbooks.com": "https://www.betterworldbooks.com/search/results?query={q}",
  "biblio.com": "https://www.biblio.com/search.php?title={q}&author=&stage=1",
  "microcenter.com": "https://www.microcenter.com/search/search_results.aspx?Ntt={q}",
  "newegg.com": "https://www.newegg.com/p/pl?d={q}",
  "target.com": "https://www.target.com/s?searchTerm={q}",
  "thriftbooks.com": "https://www.thriftbooks.com/browse/?b.search={q}#b.s=price-asc&b.p=1&b.pp=50&b.oos",
  "walmart.com": "https://www.walmart.com/search?q={q}"
};
