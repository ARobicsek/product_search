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

import re
from collections.abc import Iterable

from product_search.models import Listing
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


# ---------------------------------------------------------------------------
# Deterministic alias-match pre-pass (Phase 41 / ADR-145)
#
# ``match.aliases`` are known, distinctive strings (MPNs, SKU forms, marketing
# phrases). A listing whose TITLE contains an exact alias is the requested
# product by construction, so it should be surfaced deterministically — at zero
# LLM cost and with zero hallucination — instead of trusting the relevance LLM,
# which on a real 134-listing RAM run DROPPED exact parts (qwen-coder kept 11 of
# them; Haiku fabricated a "Refurbished" rejection on the exact part
# HMCG84AGBRA191N that the profile's empty condition_in actually allowed). The
# LLM then judges only the fuzzy remainder.
# ---------------------------------------------------------------------------

# Condition cues for the pre-pass guard. We only need to detect a condition an
# ACTIVE condition_in rule would EXCLUDE, so an exact-alias listing whose title
# plainly states a disallowed condition is routed to the LLM remainder rather
# than auto-passed. Word-boundary anchored so "used" never fires on
# "unused"/"amused".
_CONDITION_CUES: dict[str, re.Pattern[str]] = {
    "used": re.compile(r"\b(?:used|pre-?owned)\b", re.IGNORECASE),
    "refurbished": re.compile(r"\b(?:refurb(?:ished)?|renewed|recertified)\b", re.IGNORECASE),
    "open box": re.compile(r"\bopen[-\s]?box\b", re.IGNORECASE),
    "new": re.compile(r"\b(?:brand new|new with tags|nwt|factory sealed|sealed)\b", re.IGNORECASE),
}


def _normalize(text: str) -> str:
    """Lowercase and collapse internal whitespace to single spaces."""
    return " ".join(text.lower().split())


def title_has_exact_alias(title: str, aliases: Iterable[str]) -> bool:
    """True when any alias appears as a case-insensitive substring of the title.

    TITLE ONLY — never the URL: Serper's only link is a ``google.com/search``
    cluster redirect that embeds the search query (which contains the alias) in
    ``?q=``, so URL matching would auto-pass every recalled listing. Matching is
    exact substring (whitespace-collapsed) so sibling SKUs (…190N vs …191N, -CWM
    vs -CWMK) do NOT match unless that exact string is itself a listed alias.
    """
    hay = _normalize(title)
    return any((norm := _normalize(a)) and norm in hay for a in aliases)


def title_states_excluded_condition(title: str, condition_in: Iterable[str]) -> bool:
    """True when the title plainly states a condition an active rule excludes.

    Empty ``condition_in`` means "allow all" → always False.
    """
    allowed = {c.strip().lower() for c in condition_in if c and c.strip()}
    if not allowed:
        return False
    return any(
        cond not in allowed and pattern.search(title)
        for cond, pattern in _CONDITION_CUES.items()
    )


def partition_by_exact_alias(
    listings: list[Listing], profile: ProfileV2
) -> tuple[list[Listing], list[Listing]]:
    """Split listings into (auto-pass exact-alias hits, remainder-for-LLM).

    A listing whose title contains an exact ``match.aliases`` string is surfaced
    deterministically — UNLESS the title plainly states a condition an active
    ``filters.condition_in`` rule excludes, in which case it is routed to the
    remainder for the (now fabrication-resistant) LLM to judge. The deterministic
    edge filters (title_excludes, in_stock, structured-condition, …) have already
    run upstream, so an alias hit here has cleared those. With no aliases declared
    the split is a no-op (everything is remainder).
    """
    aliases = [a for a in profile.match.aliases if a and a.strip()]
    if not aliases:
        return [], list(listings)
    condition_in = profile.filters.condition_in or []
    hits: list[Listing] = []
    remainder: list[Listing] = []
    for lst in listings:
        if title_has_exact_alias(lst.title, aliases) and not title_states_excluded_condition(
            lst.title, condition_in
        ):
            hits.append(lst)
        else:
            remainder.append(lst)
    return hits, remainder
