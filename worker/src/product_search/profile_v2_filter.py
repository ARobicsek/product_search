"""Translate a ``ProfileV2`` into the v1 filter shape (Phase 32, ADR).

"Keep core, redesign edges" (REBUILD_PLAN §2): the deterministic filter
(``validators/filters.apply_filters``) and the LLM relevance filter
(``validators/ai_filter.ai_filter``) are proven and already Serper-aware
(Phase 31). Rather than fork them for v2, we build a lightweight v1 ``Profile``
carrying exactly the fields those two functions read — ``slug``,
``display_name``, ``description``, ``target``, ``spec_filters`` — from the v2
"query + spec" profile. The v1 ``Profile`` model only requires
``slug``/``display_name``/``target`` and allows extras, so this is a clean,
no-edit reuse.

This coupling to v1 ``Profile`` is transitional: Phase 36 retires the v1
scraping pipeline, at which point the filter core can take a native v2 spec and
this shim goes away.

Mapping:
* ``filters.condition_in`` → ``{rule: condition_in, values: [...]}``
* ``filters.in_stock``     → ``{rule: in_stock}``
* ``match.title_excludes`` → ``{rule: title_excludes, values: [...]}``
* always ``{rule: single_sku_url}`` so the Serper offer-link exception
  (ADR-131 P0, already in filters.py + the ai_filter prompt) is exercised
* ``match.aliases`` are folded into the description so they reach the ai_filter
  LLM as "any of these confirms the right product" (v1 ai_filter never read
  ``match_aliases`` directly — it only sees ``display_name`` + ``description``).

``filters.min_quantity`` is intentionally NOT mapped: it is honored only for
eBay listings (real quantity), and Phase 32 recall is Serper-only (quantity
unknown → would pass anyway). The run reports it as a ``degraded_attr`` note
(REBUILD_PLAN §8) instead of silently dropping every listing.
"""

from __future__ import annotations

from collections.abc import Iterable

from product_search.profile import FilterRule, Profile, Source
from product_search.profile_v2 import ProfileV2

# The v1 ``Profile`` model requires a non-empty ``sources`` list, but the filter
# core (``apply_filters`` / ``ai_filter``) never reads ``sources`` — recall in v2
# is the query, not a vendor list. A single harmless known source id satisfies
# the Pydantic constraint without affecting filtering. (``ebay_search`` carries
# no extra per-source validation; ``universal_ai_search`` would require a URL.)
_FILTER_SHIM_SOURCES = [Source(id="ebay_search")]


def build_filter_description(profile: ProfileV2) -> str:
    """The "what the user wants" text for the ai_filter prompt.

    Folds ``match.aliases`` in so the distinctive model numbers / SKU forms /
    marketing phrases reach the relevance LLM, which otherwise only sees
    ``display_name`` + ``description``.
    """
    desc = (profile.description or "").strip() or profile.display_name
    aliases = [a.strip() for a in profile.match.aliases if a and a.strip()]
    if aliases:
        desc += (
            "\nKnown identifiers / aliases (any of these in the title confirms "
            "the right product): " + ", ".join(aliases)
        )
    return desc


def build_spec_filters(profile: ProfileV2) -> list[FilterRule]:
    """Build the v1 ``spec_filters`` list from a v2 profile's filters + match."""
    rules: list[FilterRule] = [FilterRule(rule="single_sku_url")]

    f = profile.filters
    if f.condition_in:
        rules.append(FilterRule.model_validate({"rule": "condition_in", "values": list(f.condition_in)}))
    if f.in_stock:
        rules.append(FilterRule(rule="in_stock"))

    excludes = [t.strip() for t in profile.match.title_excludes if t and t.strip()]
    if excludes:
        rules.append(FilterRule.model_validate({"rule": "title_excludes", "values": excludes}))

    return rules


def distinctive_aliases(profile: ProfileV2) -> list[str]:
    """The alias strings used as exact-match keys (the strict gate + the
    ``ai_filter`` per-listing model-name signal).

    Just the stripped, non-empty ``match.aliases``: ``ProfileV2`` already enforces
    that every alias is DISTINCTIVE (contains a digit or is a multi-word phrase —
    ADR-099, so a bare generic word can't flag/gate a whole catalog), and the v1
    ``Profile.match_aliases`` field carries the same validator, so these are
    always safe to pass straight through. Kept as one helper so the gate and the
    signal always agree on "what counts".
    """
    return [a.strip() for a in profile.match.aliases if a and a.strip()]


def to_filter_profile(profile: ProfileV2) -> Profile:
    """Build a v1 ``Profile`` carrying just what the filter core reads."""
    return Profile(
        slug=profile.slug,
        display_name=profile.display_name,
        description=build_filter_description(profile),
        target=profile.target,
        spec_filters=build_spec_filters(profile),
        sources=list(_FILTER_SHIM_SOURCES),
        # The relevance LLM reads this as a per-listing "model name present"
        # signal (ai_filter); the strict gate uses the same set (ADR-150).
        match_aliases=distinctive_aliases(profile),
    )


# ---------------------------------------------------------------------------
# Alias matching (Phase 41 / ADR-145, redesigned ADR-150)
#
# ``match.aliases`` are known, distinctive strings (MPNs, SKU forms, marketing
# phrases). A title that carries an exact alias as a DISTINCT token is strong
# evidence the listing is the requested product — but NOT proof: accessory and
# compatible-part listings routinely name the model they fit ("ear pads for
# Clear MG", "32GB memory for H14SSL-N", "I/O shield ... H14SSL-N"). The earlier
# design AUTO-PASSED any alias-substring hit, which surfaced exactly those
# accessories in prod (focal/supermicro, 2026-06-28). So the alias match is now
# used two ways, never as a blind auto-pass: as a GATE for exact-SKU asks
# (``variant_strict``) and as a per-listing SIGNAL handed to the relevance LLM
# (see ``run_v2`` step 4 + ``ai_filter``). The LLM always makes the final call.
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase and collapse internal whitespace to single spaces."""
    return " ".join(text.lower().split())


def title_has_exact_alias(title: str, aliases: Iterable[str]) -> bool:
    """True when any alias appears in the title as a distinct (token-bounded) match.

    TITLE ONLY — never the URL: Serper's only link is a ``google.com/search``
    cluster redirect that embeds the search query (which contains the alias) in
    ``?q=``, so URL matching would match every recalled listing. Matching is
    case-insensitive and whitespace-collapsed, and requires a token boundary on
    each side (the adjacent character must be non-alphanumeric or the string
    edge). That boundary is load-bearing: it stops the alias ``H14SSL-N`` from
    matching the DIFFERENT SKU ``H14SSL-NT``, while still matching ``H14SSL-N``
    next to a hyphen/space (``MBD-H14SSL-N-O``); sibling SKUs (…190N vs …191N)
    never match unless that exact string is itself a listed alias.
    """
    hay = _normalize(title)
    for a in aliases:
        norm = _normalize(a)
        if not norm:
            continue
        start = 0
        while (i := hay.find(norm, start)) >= 0:
            before_ok = i == 0 or not hay[i - 1].isalnum()
            after = i + len(norm)
            after_ok = after >= len(hay) or not hay[after].isalnum()
            if before_ok and after_ok:
                return True
            start = i + 1
    return False
