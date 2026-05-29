"""Classify why a source returned 0 results, for the daily report (ADR-084).

A bare "0" in the report's "Sources searched" table is far less useful than a
*reason*: was the vendor genuinely empty, was it a transient scraping glitch
that the next run will clear, is the vendor permanently un-scrapeable today, or
did we fetch a real page but fail to parse it (a gap on our side)?

This module is the deterministic classifier. It takes only plain data (counts,
the raw error string, the per-fetch diagnostics dict from ``universal_ai``, and
the vendor's ``known_failure`` registry entry) and returns a category + a
plain-English message. No LLM, no network, no cli import — so the report's
synthesizer post-check (which forbids fabricated numbers) never sees it, and it
is trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# A body shorter than this is not a real rendered search page — most likely an
# error stub, an empty shell, or a thin "no results" page. At or above it, a
# 0-candidate outcome is more likely a parser gap on our side than a truly
# empty result set. This is a heuristic boundary (the EMPTY_PAGE vs PARSER_GAP
# split can't be made perfectly deterministic), so the messages hedge.
SUBSTANTIVE_BODY_FLOOR = 50_000

# A body *below* this ceiling with 0 candidates is almost certainly a 404 stub,
# bot-block interstitial, or wrong-URL page — NOT proof the product isn't sold
# there.  ADR-098 fix #3: classify as TRANSIENT ("check the URL") instead of
# EMPTY_PAGE ("genuinely has nothing"), which was actively misleading.
THIN_BODY_CEILING = 15_000

# ADR-099: the carry-gate (universal_ai.fetch) writes a skip reason starting
# with this prefix when it deliberately skips the paid LLM extractors because
# the product's identifier (family-core model token / match_aliases) isn't on
# the fetched page. The classifier recognizes the prefix and returns WATCHED —
# a calm, distinct "not stocked here yet" status that must NEVER be conflated
# with a transient glitch or an error. ``universal_ai`` imports this constant
# so the producer and the classifier can't drift apart.
WATCH_GATE_REASON_PREFIX = "watch-gate:"


class OutcomeCategory(StrEnum):
    """Why a source produced the result it did (only the non-OK ones surface)."""

    OK = "ok"                  # passed > 0; nothing to explain
    NO_MATCH = "no_match"      # fetched listings, none met the profile criteria
    EMPTY_PAGE = "empty_page"  # page loaded but had no products (genuinely empty)
    PARSER_GAP = "parser_gap"  # full page fetched, 0 parsed — likely our gap
    TRANSIENT = "transient"    # scraping glitch; likely clears on the next run
    PERMANENT = "permanent"    # no working path today (blocked / auth / quota)
    WATCHED = "watched"        # carry-gate skipped paid extraction; not stocked yet (ADR-099)


# Short human label shown as the bold tag in the report callout.
CATEGORY_LABEL: dict[OutcomeCategory, str] = {
    OutcomeCategory.NO_MATCH: "no match",
    OutcomeCategory.EMPTY_PAGE: "no results",
    OutcomeCategory.PARSER_GAP: "needs work",
    OutcomeCategory.TRANSIENT: "transient",
    OutcomeCategory.PERMANENT: "blocked",
    OutcomeCategory.WATCHED: "watched",
}


@dataclass(frozen=True)
class SourceOutcome:
    category: OutcomeCategory
    message: str
    custom_label: str | None = None

    @property
    def label(self) -> str:
        if self.custom_label:
            return self.custom_label
        return CATEGORY_LABEL.get(self.category, self.category.value)

    @property
    def is_clean(self) -> bool:
        return self.category is OutcomeCategory.OK


def _short_error(error: str) -> str:
    """One-line, truncated form of a raw exception string for a report cell."""
    one_line = " ".join(str(error).split())
    return one_line[:160]


def _looks_like_quota_or_auth(error: str) -> bool:
    low = str(error).lower()
    return "quota" in low or "auth" in low or "429" in low or "401" in low or "403" in low


def classify_source_outcome(
    *,
    fetched: int,
    passed: int,
    error: str | None = None,
    skip_reason: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    known_failure: dict[str, Any] | None = None,
    dominant_rejection: str | None = None,
) -> SourceOutcome:
    """Classify one source's outcome into a category + plain-English reason.

    ``diagnostics`` is ``universal_ai.LAST_FETCH_DIAGNOSTICS`` for the source
    (``None`` if the source was skipped before any fetch, or isn't a
    universal_ai source). ``known_failure`` is the vendor's registry
    ``known_failure`` block (``None`` if the vendor has no known failure).
    """
    diag = diagnostics or {}

    # 1. Success — nothing to explain.
    if passed > 0:
        return SourceOutcome(OutcomeCategory.OK, "")

    # 2. We fetched listings but none survived the filter. The fetch itself
    #    worked, so this is a genuine "no qualifying result", not a failure —
    #    classify it before any degraded/transient signal.
    if fetched > 0:
        plural = "listing" if fetched == 1 else "listings"
        if dominant_rejection is not None and dominant_rejection.startswith("mis_scoped_url_"):
            n_rejected = dominant_rejection.split("_")[-1]
            return SourceOutcome(
                OutcomeCategory.NO_MATCH,
                f"Found {fetched} {plural} but none matched what you're "
                f"tracking — this source's search URL may be mis-scoped "
                f"(returning unrelated products). **What to do:** open "
                f"**Edit Profile** and check or replace the search URL for "
                f"this vendor. The AI filter diagnostic below lists the "
                f"specific rejections.",
                custom_label=f"NO_MATCH (Mis-scoped URL; {n_rejected} listings rejected by filter)",
            )
        # ADR-098 fix #4: when the dominant rejection reason is relevance_check,
        # the URL is probably mis-scoped (returning unrelated products), not a
        # filter that needs loosening.
        if dominant_rejection == "relevance_check":
            return SourceOutcome(
                OutcomeCategory.NO_MATCH,
                f"Found {fetched} {plural} but none matched what you're "
                f"tracking — this source's search URL may be mis-scoped "
                f"(returning unrelated products). **What to do:** open "
                f"**Edit Profile** and check or replace the search URL for "
                f"this vendor. The AI filter diagnostic below lists the "
                f"specific rejections.",
            )
        return SourceOutcome(
            OutcomeCategory.NO_MATCH,
            f"Found {fetched} {plural} but none met your search criteria "
            f"(price, condition, or keyword filters). **What to do:** nothing, "
            f"unless you expected matches here — if so, open **Edit Profile** "
            f"and loosen the relevant filter (price cap, condition, keywords). "
            f"The AI filter diagnostic below lists the specific rejections.",
        )

    # ADR-124: ``vendor_does_not_carry`` is set by cli.annotate_dominant_rejections
    # for EVERY source with fetched == 0 — so it must be the LAST-RESORT verdict,
    # evaluated only after the carry-gate (WATCHED), transient/bot-wall, and
    # parser-gap branches below have been ruled out. It used to be checked HERE
    # (ADR-112), which made all of those more-specific diagnoses unreachable for
    # any zero-fetch universal source: a bot-walled Amazon page, an AlterLab
    # render failure, or a carry-gate skip all wrongly reported "Vendor doesn't
    # carry — re-running won't change anything." The check now lives next to the
    # EMPTY_PAGE fallback at the bottom of this function.

    # 2.5 ADR-099 carry-gate: we fetched the page but deliberately skipped the
    #     paid LLM extractors because the product's identifier (family-core
    #     model token / match_aliases) wasn't on it. This is NOT an error and
    #     NOT a transient glitch — the vendor simply isn't listing the product
    #     yet, and we spent ~$0 confirming that. Say exactly that.
    if skip_reason and skip_reason.startswith(WATCH_GATE_REASON_PREFIX):
        return SourceOutcome(
            OutcomeCategory.WATCHED,
            "We checked this vendor and your product isn't listed there yet, so "
            "we skipped the paid extraction step — this source cost about $0 this "
            "run. **What to do:** nothing — every scheduled run re-checks this "
            "vendor and will pull listings automatically the moment it stocks the "
            "product.",
        )

    # 3. Vendor flagged in the registry as a known failure (blocker) — an anti-bot wall
    #    with no working path. Re-running won't help; only deeper scraper work
    #    can recover it (and may not). Use the registry's own summary so the
    #    report and the onboarder tell the same story. Do NOT promise an
    #    automatic fix — these are parked, not actively in flight.
    if known_failure and known_failure.get("severity") == "blocker":
        summary = " ".join(str(known_failure.get("summary", "")).split())
        detail = f" {summary}" if summary else ""
        return SourceOutcome(
            OutcomeCategory.PERMANENT,
            f"This vendor is blocked — an anti-bot wall we have no working path "
            f"through right now — so re-running won't help. **What to do:** "
            f"nothing in the app recovers this; getting it working needs deeper "
            f"scraper changes and isn't guaranteed.{detail}",
        )

    # 4. Quota / auth error — structural; listings can't be fetched until the
    #    operator fixes the API account.
    if error and _looks_like_quota_or_auth(error):
        return SourceOutcome(
            OutcomeCategory.PERMANENT,
            "The scraping API returned a quota or authentication error, so no "
            "vendor could be fetched until the account is fixed. **What to do:** "
            "check your AlterLab / eBay dashboard limits, then run again.",
        )

    # 5. The run skipped this source (circuit breaker open / per-run budget
    #    spent). That's a downstream effect of AlterLab being degraded earlier
    #    in the run — transient.
    if skip_reason:
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            f"Skipped this run because AlterLab was failing on earlier sources "
            f"({_short_error(skip_reason)}). **What to do:** run this product "
            f"again once AlterLab recovers — the next scheduled run also retries "
            f"automatically.",
        )

    # 6. AlterLab's browser pool was exhausted — a transient capacity issue on
    #    the scraping provider (ADR-083).
    if diag.get("alterlab_pool_exhausted"):
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            "AlterLab's browser pool was briefly exhausted — a temporary "
            "capacity issue at the scraping provider. **What to do:** run this "
            "product again in a few minutes; it usually clears on its own and "
            "the next scheduled run retries automatically.",
        )

    # 7. AlterLab couldn't deliver a usable rendered body (5xx-exhausted, or fell
    #    through to a cheaper fetcher that bot-walled vendors block).
    if diag.get("alterlab_degraded"):
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            "AlterLab couldn't render this vendor's page this time, so nothing "
            "could be extracted. **What to do:** run this product again — this "
            "is usually temporary and the next scheduled run retries "
            "automatically.",
        )

    # 8. Some other fetch error (timeout, connection reset, …). Most are
    #    transient; surface the short form so it's debuggable from the report.
    if error:
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            f"Fetch error — usually temporary. **What to do:** run this product "
            f"again. Details: {_short_error(error)}",
        )

    # 9. No error, but 0 candidates. Distinguish a real (substantive) page that
    #    we failed to parse from a genuinely empty result set, by body size.
    body_len = int(diag.get("body_len") or 0)
    if body_len >= SUBSTANTIVE_BODY_FLOOR:
        return SourceOutcome(
            OutcomeCategory.PARSER_GAP,
            f"Fetched a full page ({body_len:,} chars) but couldn't read any "
            f"product listings off it — the page rendered, but our reader didn't "
            f"recognise this vendor's layout (not a true empty result). **What "
            f"to do:** open **Edit Profile** and add the vendor's product-page "
            f"(detail) URL — that path extracts more reliably. If that also "
            f"returns nothing, it needs a scraper fix (re-running won't help).",
        )

    # ADR-098 fix #3: a tiny body (under THIN_BODY_CEILING) with 0 candidates
    # is almost certainly a 404 stub, bot-block page, or wrong-URL page — NOT
    # proof the product isn't sold there.  Classify as TRANSIENT so the user
    # gets honest "check the URL" guidance instead of "genuinely has nothing."
    if 0 < body_len < THIN_BODY_CEILING:
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            f"The vendor's page returned an unusually small body "
            f"({body_len:,} chars) with no products — most likely a 404 stub, "
            f"bot-block, or wrong URL, not proof the product isn't sold there. "
            f"**What to do:** open **Edit Profile** and check the search URL; "
            f"re-running may also help if this was a transient block.",
        )

    # ADR-124: we reached the bottom — a real, substantively-sized page (or a
    # source with no diagnostics at all) that fetched 0 listings, with no
    # carry-gate skip, no transient/degraded signal, and no error. THIS is the
    # only place "the vendor genuinely has nothing" is a fair conclusion. When
    # cli stamped vendor_does_not_carry (fetched == 0), use the NO_MATCH label;
    # otherwise the generic EMPTY_PAGE fallback (e.g. tests / non-universal
    # sources that never get a dominant_rejection).
    if dominant_rejection == "vendor_does_not_carry":
        return SourceOutcome(
            OutcomeCategory.NO_MATCH,
            "The vendor's page loaded but the extractor found no products — most "
            "likely it genuinely has nothing right now, so re-running won't change "
            "anything. **What to do:** if you expected results, open **Edit "
            "Profile** and check the search URL / keywords.",
            custom_label="NO_MATCH (Vendor doesn't carry)",
        )

    return SourceOutcome(
        OutcomeCategory.EMPTY_PAGE,
        "The vendor's page loaded but had no matching products — most likely it "
        "genuinely has nothing right now, so re-running won't change anything. "
        "**What to do:** if you expected results, open **Edit Profile** and "
        "check the search URL / keywords, or ask the onboarder to re-probe it "
        "if you think it timed out during onboarding; otherwise nothing — "
        "scheduled runs will catch it when the vendor lists a match.",
    )
