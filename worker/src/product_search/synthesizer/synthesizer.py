"""Synthesizer — render today's listings + diff into a markdown report.

Public entry point is :func:`synthesize`. It calls the configured LLM
with the prompt at ``prompts/synth_v1.txt`` and runs :func:`post_check`
on the output. Per ADR-001 the run fails loud if any price, URL, MPN,
or quantity in the report does not appear in the input — we'd rather
miss a daily report than commit fabricated data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, cast

from product_search.llm import Message, ProviderName, call_llm
from product_search.models import Listing
from product_search.profile import Profile
from product_search.storage.diff import DiffResult

PROMPT_NAME = "synth_v1.txt"


class PostCheckError(RuntimeError):
    """Raised when the synth output contains numbers/URLs not in the input."""


@dataclass
class SynthesisResult:
    report_md: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    prompt_chars: int


# ---------------------------------------------------------------------------
# Input shaping
# ---------------------------------------------------------------------------


# Cap on listings sent to the LLM. Phase 5 fixtures had ~5–10 listings;
# the live eBay path returns 100+. Above this cap, the synth model produces
# an empty response (max_tokens hit) or refuses. The full set is still in
# SQLite and the daily CSV; the worker appends a deterministic full-table
# section after the synthesized markdown so nothing is lost.
SYNTH_MAX_LISTINGS = 30


def build_input_payload(
    listings: list[Listing],
    diff: DiffResult | None,
    profile: Profile,
    *,
    snapshot_date: _date | None = None,
    max_listings: int = SYNTH_MAX_LISTINGS,
) -> dict[str, Any]:
    """Shape the JSON payload the LLM sees.

    Listings are sorted by ``total_for_target_usd`` ascending (nulls last)
    so a model that just iterates the input also produces a correctly
    ordered table. Capped at ``max_listings`` rows — the worker writes the
    full table separately as a deterministic appendix.
    """

    def _key(lst: Listing) -> tuple[int, float]:
        if lst.total_for_target_usd is None:
            return (1, 0.0)
        return (0, lst.total_for_target_usd)

    sorted_listings = sorted(listings, key=_key)
    truncated = sorted_listings[:max_listings]
    listings_json = [lst.to_dict() for lst in truncated]

    diff_json: dict[str, Any] | None
    if diff is None:
        diff_json = None
    else:
        diff_json = {
            "new": [lst.to_dict() for lst in diff.new],
            "dropped": [lst.to_dict() for lst in diff.dropped],
            "changed": [
                {
                    "url": ch.url,
                    "title": ch.title,
                    "old_price_usd": ch.old_price_usd,
                    "new_price_usd": ch.new_price_usd,
                    "pct_change": round(ch.pct_change, 4),
                }
                for ch in diff.changed
            ],
        }

    return {
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "product": {
            "slug": profile.slug,
            "display_name": profile.display_name,
            "target": profile.target.model_dump(),
            "synthesis_hints": profile.synthesis_hints,
        },
        "listings": listings_json,
        "diff": diff_json,
    }


def render_prompt() -> str:
    """Return the system prompt text from ``prompts/synth_v1.txt``."""
    prompt_path = Path(__file__).parent / "prompts" / PROMPT_NAME
    return prompt_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Post-check
# ---------------------------------------------------------------------------

# Numbers like 1234, 1234.56, .5
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+")
# URLs (http/https). Stop at whitespace or common closing punctuation.
_URL_RE = re.compile(r"https?://[^\s)\]>'\"]+")
# Trailing punctuation that often follows a URL in markdown
_URL_TRAIL = ".,;:)]}>'\""


def _normalize_number(n: str) -> str:
    """Canonicalise so 100, 100.0, 100.00 all compare equal."""
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n if n != "" else "0"


def _extract_numbers(text: str) -> set[str]:
    return {_normalize_number(n) for n in _NUMBER_RE.findall(text)}


def _extract_urls(text: str) -> set[str]:
    return {raw.rstrip(_URL_TRAIL) for raw in _URL_RE.findall(text)}


def post_check(report_md: str, payload: dict[str, Any]) -> None:
    """Raise :class:`PostCheckError` if the report fabricates data.

    The check tokenises numbers and URLs out of the markdown and verifies
    each one appears in (or is a substring of) the input payload. Rank
    numbers (1..N) and a small allowlist for prompt-mentioned constants
    ("5", "100", "200") are always permitted.
    """
    payload_text = json.dumps(payload, sort_keys=True, default=str)
    payload_numbers = _extract_numbers(payload_text)

    n_listings = len(payload.get("listings") or [])
    rank_max = max(20, n_listings + 5)
    allowed_numbers = {str(i) for i in range(rank_max + 1)}
    # Constants the prompt itself mentions: 5% threshold, 100% rating,
    # 200-word cap.
    allowed_numbers |= {"5", "100", "200"}

    report_numbers = _extract_numbers(report_md)

    # Allow fraction→percent conversion (e.g. pct_change 0.056 → "5.6%").
    # If N is in the report and N/100 is in the payload, accept it.
    pct_allowed: set[str] = set()
    for pn in payload_numbers:
        try:
            pct_allowed.add(_normalize_number(f"{float(pn) * 100:.6f}"))
            pct_allowed.add(_normalize_number(f"{abs(float(pn)) * 100:.6f}"))
        except ValueError:
            continue

    bad_numbers = sorted(
        n
        for n in report_numbers
        if n not in payload_numbers
        and n not in allowed_numbers
        and n not in pct_allowed
        and not any(n in pn for pn in payload_numbers)
    )

    payload_urls = _extract_urls(payload_text)
    report_urls = _extract_urls(report_md)
    bad_urls = sorted(u for u in report_urls if u not in payload_urls)

    problems: list[str] = []
    if bad_numbers:
        problems.append(f"fabricated numbers: {bad_numbers}")
    if bad_urls:
        problems.append(f"fabricated URLs: {bad_urls}")

    if problems:
        raise PostCheckError(
            "Synthesizer post-check failed: " + "; ".join(problems)
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def synthesize(
    listings: list[Listing],
    diff: DiffResult | None,
    profile: Profile,
    *,
    provider: str,
    model: str,
    snapshot_date: _date | None = None,
    max_tokens: int = 4096,
) -> SynthesisResult:
    """Build the payload, call the LLM, post-check, return the report."""
    payload = build_input_payload(listings, diff, profile, snapshot_date=snapshot_date)
    system_prompt = render_prompt()
    user_content = json.dumps(payload, default=str, indent=2)

    resp = call_llm(
        provider=cast(ProviderName, provider),
        model=model,
        system=system_prompt,
        messages=[Message(role="user", content=user_content)],
        max_tokens=max_tokens,
    )

    report_md = resp.text.strip()
    post_check(report_md, payload)

    return SynthesisResult(
        report_md=report_md,
        provider=provider,
        model=model,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        prompt_chars=len(system_prompt) + len(user_content),
    )
