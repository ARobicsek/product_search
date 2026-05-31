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


def to_filter_profile(profile: ProfileV2) -> Profile:
    """Build a v1 ``Profile`` carrying just what the filter core reads."""
    return Profile(
        slug=profile.slug,
        display_name=profile.display_name,
        description=build_filter_description(profile),
        target=profile.target,
        spec_filters=build_spec_filters(profile),
        sources=list(_FILTER_SHIM_SOURCES),
    )
