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


class OutcomeCategory(StrEnum):
    """Why a source produced the result it did (only the non-OK ones surface)."""

    OK = "ok"                  # passed > 0; nothing to explain
    NO_MATCH = "no_match"      # fetched listings, none met the profile criteria
    EMPTY_PAGE = "empty_page"  # page loaded but had no products (genuinely empty)
    PARSER_GAP = "parser_gap"  # full page fetched, 0 parsed — likely our gap
    TRANSIENT = "transient"    # scraping glitch; likely clears on the next run
    PERMANENT = "permanent"    # no working path today (blocked / auth / quota)


# Short human label shown as the bold tag in the report callout.
CATEGORY_LABEL: dict[OutcomeCategory, str] = {
    OutcomeCategory.NO_MATCH: "no match",
    OutcomeCategory.EMPTY_PAGE: "no results",
    OutcomeCategory.PARSER_GAP: "needs work",
    OutcomeCategory.TRANSIENT: "transient",
    OutcomeCategory.PERMANENT: "blocked",
}


@dataclass(frozen=True)
class SourceOutcome:
    category: OutcomeCategory
    message: str

    @property
    def label(self) -> str:
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
        return SourceOutcome(
            OutcomeCategory.NO_MATCH,
            f"Found {fetched} {plural} but none met your search criteria "
            f"(price, condition, or keyword filters). See the AI filter "
            f"diagnostic below for the specific rejection reasons.",
        )

    # 3. Vendor flagged in the registry as a known failure — permanent until
    #    someone does the AlterLab/anti-bot work. Use the registry's own
    #    summary so the report and the onboarder tell the same story.
    if known_failure:
        summary = " ".join(str(known_failure.get("summary", "")).split())
        detail = f" {summary}" if summary else ""
        return SourceOutcome(
            OutcomeCategory.PERMANENT,
            f"This vendor has no working scrape path today and won't recover "
            f"without further work on our side.{detail}",
        )

    # 4. Quota / auth error — structural; listings can't be fetched until the
    #    operator fixes the API account.
    if error and _looks_like_quota_or_auth(error):
        return SourceOutcome(
            OutcomeCategory.PERMANENT,
            "The scraping API returned a quota or authentication error. Check "
            "your AlterLab / eBay dashboard limits — listings can't be fetched "
            "until that's resolved.",
        )

    # 5. The run skipped this source (circuit breaker open / per-run budget
    #    spent). That's a downstream effect of AlterLab being degraded earlier
    #    in the run — transient.
    if skip_reason:
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            f"Skipped this run because AlterLab was failing on earlier sources "
            f"({_short_error(skip_reason)}). Likely resolves on the next run.",
        )

    # 6. AlterLab's browser pool was exhausted — a transient capacity issue on
    #    the scraping provider (ADR-083).
    if diag.get("alterlab_pool_exhausted"):
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            "AlterLab's browser pool was exhausted (a transient capacity issue "
            "on the scraping provider). This usually clears on its own — the "
            "next scheduled run will likely succeed.",
        )

    # 7. AlterLab couldn't deliver a usable rendered body (5xx-exhausted, or fell
    #    through to a cheaper fetcher that bot-walled vendors block).
    if diag.get("alterlab_degraded"):
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            "AlterLab was degraded and couldn't render this vendor's page, so "
            "no listings could be extracted. Likely resolves on the next run.",
        )

    # 8. Some other fetch error (timeout, connection reset, …). Most are
    #    transient; surface the short form so it's debuggable from the report.
    if error:
        return SourceOutcome(
            OutcomeCategory.TRANSIENT,
            f"Fetch error — may resolve on the next run: {_short_error(error)}",
        )

    # 9. No error, but 0 candidates. Distinguish a real (substantive) page that
    #    we failed to parse from a genuinely empty result set, by body size.
    body_len = int(diag.get("body_len") or 0)
    if body_len >= SUBSTANTIVE_BODY_FLOOR:
        return SourceOutcome(
            OutcomeCategory.PARSER_GAP,
            f"Fetched a full page ({body_len:,} chars) but couldn't parse any "
            f"product listings — most likely a parser gap with this vendor's "
            f"page structure rather than a true empty result. Recoverable with "
            f"extractor work on our side.",
        )

    return SourceOutcome(
        OutcomeCategory.EMPTY_PAGE,
        "The vendor's page loaded but contained no matching products — most "
        "likely the search genuinely returned nothing right now.",
    )
