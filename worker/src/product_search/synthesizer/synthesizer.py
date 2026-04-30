"""Synthesizer — render today's listings + diff into a markdown report.

Architecture (ADR-028, supersedes the structure left after ADR-027):

The LLM contributes ONE qualitative paragraph (the **Context** section).
Every other report section — Bottom line, Ranked listings, Diff,
Flags — is built deterministically from the verified listing data. The
post-check still rejects any digit in the LLM's paragraph that isn't
present in the input payload, but with the LLM's job narrowed to
qualitative prose the failure mode "model fabricates a percentage in a
narrative comparison" can no longer originate inside a fact-laden
sentence we asked the model to write.

Public entry point is :func:`synthesize`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
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
    """Raised when the synth output contains numbers not in the input."""

    def __init__(self, message: str, bad_numbers: list[str] | None = None) -> None:
        super().__init__(message)
        self.bad_numbers: list[str] = bad_numbers or []


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

SYNTH_MAX_LISTINGS = 30


def build_input_payload(
    listings: list[Listing],
    diff: DiffResult | None,
    profile: Profile,
    *,
    snapshot_date: _date | None = None,
    max_listings: int = SYNTH_MAX_LISTINGS,
) -> dict[str, Any]:
    """Shape the JSON payload the LLM sees."""

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
# Python Tabular Markdown Generators
# ---------------------------------------------------------------------------

def _esc(value: str) -> str:
    return value.replace("|", "\\|")


def _money(value: float | None) -> str:
    return f"${value:.2f}" if value is not None else "unknown"


def _source_label(lst: Listing) -> str:
    """Display label for the Source column.

    For ``universal_ai_search`` rows, the literal adapter id isn't useful
    to a human reader — show the vendor's host (without ``www.``) instead,
    falling back to the URL host parsed at render time. The internal
    ``lst.source`` field is still the canonical adapter id everywhere
    else (source_stats, cost panel, SQLite); this is presentation-only.
    """
    if lst.source == "universal_ai_search":
        host = (lst.attrs or {}).get("vendor_host")
        if not host:
            from urllib.parse import urlparse
            host = urlparse(lst.url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or lst.source
    return lst.source


# Registry of available report-table columns. Each entry maps a stable
# column id (used in profile.yaml) to (header, formatter). The formatter
# receives (rank_index, listing) and returns the cell's markdown text.
# Headers and formatters live here so they stay in sync; the onboarding
# prompt's "available columns" list MUST be kept in sync with this dict.
COLUMN_DEFS: dict[str, tuple[str, Callable[[int, Listing], str]]] = {
    "rank": ("Rank", lambda i, lst: str(i)),
    "source": ("Source", lambda i, lst: f"[{_esc(_source_label(lst))}]({lst.url})"),
    "title": ("Title", lambda i, lst: _esc(lst.title)),
    "price_unit": ("Price (unit)", lambda i, lst: _money(lst.unit_price_usd)),
    "total_for_target": ("Total for target", lambda i, lst: _money(lst.total_for_target_usd)),
    "qty": (
        "Qty",
        lambda i, lst: str(lst.quantity_available) if lst.quantity_available is not None else "unknown",
    ),
    "condition": ("Condition", lambda i, lst: lst.condition or "unknown"),
    "brand": ("Brand", lambda i, lst: _esc(lst.brand) if lst.brand else "unknown"),
    "mpn": ("MPN", lambda i, lst: _esc(lst.mpn) if lst.mpn else "unknown"),
    "seller": ("Seller", lambda i, lst: _esc(lst.seller_name) if lst.seller_name else "unknown"),
    "seller_rating": (
        "Seller rating",
        lambda i, lst: f"{lst.seller_rating_pct:.1f}%" if lst.seller_rating_pct is not None else "unknown",
    ),
    "ship_from": ("Ships from", lambda i, lst: lst.ship_from_country or "unknown"),
    "qvl_status": ("QVL", lambda i, lst: lst.qvl_status or "unknown"),
    "flags": ("Flags", lambda i, lst: ", ".join(lst.flags) if lst.flags else "(no flags)"),
}

DEFAULT_REPORT_COLUMNS: list[str] = [
    "rank",
    "source",
    "title",
    "price_unit",
    "total_for_target",
    "qty",
    "seller",
    "flags",
]


# Plain-English fallback descriptions for stable flag IDs surfaced across
# profiles. Profile-level ``FlagRule.description`` always wins over this
# dict; this is just a safety net so a freshly onboarded profile that
# forgot to set ``description:`` still renders a useful Flags section.
FLAG_FALLBACK_DESCRIPTIONS: dict[str, str] = {
    "unknown_quantity": "The listing did not declare a quantity-available count.",
    "low_seller_feedback": "Seller's feedback rating or count is below the profile threshold.",
    "china_shipping": "Listing ships from China or Hong Kong.",
    "smart_memory": "OEM-branded SmartMemory; may not POST on third-party boards.",
    "kingston_e_suffix": "Kingston part number ends in 'E' (likely UDIMM, not RDIMM).",
    "compatible_with_other_server": (
        "Title mentions a different server platform than the profile target."
    ),
    "generic_brand": "Generic or aftermarket brand.",
    "suspicious_listing": "Title language suggests an as-is, untested, or for-parts listing.",
    "similar_bose_model": "Title mentions a similar but different Bose model than the target.",
}


def build_listings_table_md(
    listings: list[Listing],
    columns: list[str] | None = None,
) -> str:
    cols = columns if columns else DEFAULT_REPORT_COLUMNS
    headers = [COLUMN_DEFS[c][0] for c in cols]

    lines = ["**Ranked listings.**\n"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")

    def _key(lst: Listing) -> tuple[int, float]:
        if lst.total_for_target_usd is None:
            return (1, 0.0)
        return (0, lst.total_for_target_usd)

    sorted_listings = sorted(listings, key=_key)[:SYNTH_MAX_LISTINGS]

    for i, lst in enumerate(sorted_listings, 1):
        cells = [COLUMN_DEFS[c][1](i, lst) for c in cols]
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _cheapest_listing(listings: list[Listing]) -> Listing:
    """Return the cheapest passing listing, treating missing totals as fallback to unit price."""

    def _key(lst: Listing) -> tuple[int, float]:
        if lst.total_for_target_usd is not None:
            return (0, lst.total_for_target_usd)
        if lst.unit_price_usd is not None:
            return (1, lst.unit_price_usd)
        return (2, 0.0)

    return sorted(listings, key=_key)[0]


def build_bottom_line_md(listings: list[Listing], profile: Profile) -> str:
    """Render the deterministic **Bottom line** section.

    Picks the cheapest passing listing and emits a one-sentence summary
    using only fields verbatim from that listing. No LLM involvement, so
    no fabrication risk. Falls back to a graceful message when the
    listing set is empty (which shouldn't happen in production —
    `cli.py` writes a separate "no listings passed" report in that
    case — but keeps the function safe to call from tests).
    """
    if not listings:
        return "**Bottom line.** No listings passed the validator pipeline today."

    top = _cheapest_listing(listings)

    if top.total_for_target_usd is not None:
        price_clause = f"${top.total_for_target_usd:.2f} total for target"
    elif top.unit_price_usd is not None:
        price_clause = f"${top.unit_price_usd:.2f}"
    else:
        price_clause = "price unknown"

    seller_clause = f" from {top.seller_name}" if top.seller_name else ""
    cond_clause = f" ({top.condition})" if top.condition else ""
    title_clip = top.title if len(top.title) <= 90 else top.title[:87] + "…"
    title_clip = _esc(title_clip)

    return (
        f"**Bottom line.** Cheapest passing listing for "
        f"{_esc(profile.display_name)}: {price_clause}{seller_clause} via "
        f"[{_esc(top.source)}]({top.url}) — {title_clip}{cond_clause}."
    )


def build_flags_md(listings: list[Listing], profile: Profile) -> str:
    """Render the deterministic **Flags** section.

    One bullet per distinct flag that appears in the visible listings,
    using ``FlagRule.description`` from the profile when present, else a
    fallback from :data:`FLAG_FALLBACK_DESCRIPTIONS`, else the bare flag
    id. Output is stable (sorted) so daily reports diff cleanly.
    """
    seen: set[str] = set()
    for lst in listings:
        for f in lst.flags:
            seen.add(f)

    if not seen:
        return "**Flags.** (no flags)"

    profile_desc: dict[str, str] = {}
    for rule in profile.spec_flags:
        if rule.description and rule.flag not in profile_desc:
            profile_desc[rule.flag] = rule.description

    lines = ["**Flags.**", ""]
    for flag in sorted(seen):
        desc = (
            profile_desc.get(flag)
            or FLAG_FALLBACK_DESCRIPTIONS.get(flag)
            or "(no description)"
        )
        lines.append(f"- **{flag}**: {desc}")
    return "\n".join(lines)


def build_diff_md(diff: DiffResult | None) -> str:
    lines = ["**Diff vs yesterday.**\n"]
    if diff is None:
        lines.append("(no prior snapshot)")
        return "\n".join(lines)
        
    # New
    lines.append("- **New:**")
    if not diff.new:
        lines.append("  - (none)")
    else:
        for lst in diff.new:
            lines.append(f"  - [{lst.source}]({lst.url}) - ${lst.unit_price_usd:.2f}: {lst.title}")
            
    # Dropped
    lines.append("- **Dropped:**")
    if not diff.dropped:
        lines.append("  - (none)")
    else:
        for lst in diff.dropped:
            lines.append(f"  - [{lst.source}]({lst.url}) - ${lst.unit_price_usd:.2f}: {lst.title}")
            
    # Changed
    lines.append("- **Price-changed (>=5%):**")
    if not diff.changed:
        lines.append("  - (none)")
    else:
        for ch in diff.changed:
            lines.append(f"  - [{ch.new_listing.source}]({ch.url}) - ${ch.old_price_usd:.2f} -> ${ch.new_price_usd:.2f} ({ch.pct_change*100:+.1f}%): {ch.title}")
            
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-check
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+")


def _normalize_number(n: str) -> str:
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n if n != "" else "0"


def _extract_numbers(text: str) -> set[str]:
    return {_normalize_number(n) for n in _NUMBER_RE.findall(text)}


def post_check(report_md: str, payload: dict[str, Any]) -> None:
    """Raise :class:`PostCheckError` if the report fabricates numeric data.
    URLs are now generated programmatically by python, so we only check numbers.
    """
    payload_text = json.dumps(payload, sort_keys=True, default=str)
    payload_numbers = _extract_numbers(payload_text)

    n_listings = len(payload.get("listings") or [])
    rank_max = max(20, n_listings + 5)
    allowed_numbers = {str(i) for i in range(rank_max + 1)}
    allowed_numbers |= {"5", "100", "200"}

    report_numbers = _extract_numbers(report_md)

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

    if bad_numbers:
        raise PostCheckError(
            f"Synthesizer post-check failed: fabricated numbers: {bad_numbers}",
            bad_numbers=bad_numbers,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_CONTEXT_PREFIX_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?\**\s*context\s*[\.\:\*]*\s*",
    re.IGNORECASE,
)


def _strip_context_prefix(text: str) -> str:
    """Drop a leading ``Context.``/``**Context.**``/``5. **Context.**`` if the
    LLM emitted one despite the prompt telling it not to."""
    return _CONTEXT_PREFIX_RE.sub("", text, count=1).lstrip()


def synthesize(
    listings: list[Listing],
    diff: DiffResult | None,
    profile: Profile,
    *,
    provider: str,
    model: str,
    snapshot_date: _date | None = None,
    max_tokens: int = 1024,
) -> SynthesisResult:
    """Build the payload, ask the LLM for a Context paragraph, assemble the report.

    Per ADR-028, every numeric/structural section (Bottom line, Ranked
    listings, Diff, Flags) is built deterministically here. The LLM's only
    job is one qualitative paragraph. We still post-check that paragraph
    against the input payload — any digit the LLM emits that isn't in the
    JSON is rejected — so the no-fabricated-numbers invariant from ADR-001
    is preserved structurally rather than by prompt discipline alone.

    On a PostCheckError we retry exactly once with a stricter prompt that
    names the rejected digits. If the retry also fails the original error
    propagates and ``cli.py``'s stub-report path takes over.
    """
    payload = build_input_payload(listings, diff, profile, snapshot_date=snapshot_date)
    system_prompt = render_prompt()
    user_content = json.dumps(payload, default=str, indent=2)

    def _call(system: str) -> Any:
        return call_llm(
            provider=cast(ProviderName, provider),
            model=model,
            system=system,
            messages=[Message(role="user", content=user_content)],
            max_tokens=max_tokens,
        )

    resp = _call(system_prompt)
    context_text = _strip_context_prefix(resp.text.strip())
    total_input_tokens = resp.input_tokens or 0
    total_output_tokens = resp.output_tokens or 0

    try:
        post_check(context_text, payload)
    except PostCheckError as exc:
        import sys

        print(
            f"[synth] post-check rejected {exc.bad_numbers} in Context; "
            f"retrying once with stricter prompt",
            file=sys.stderr,
        )
        retry_system = (
            system_prompt
            + "\n\n# RETRY — PRIOR ATTEMPT REJECTED\n\n"
            + "Your previous Context paragraph was REJECTED because it "
            + "contained digit-tokens NOT present in the input JSON: "
            + f"{exc.bad_numbers}. These were almost certainly computed "
            + "comparisons (percentages, ratios, savings, averages).\n\n"
            + "Re-emit the Context paragraph using ONLY qualitative "
            + "phrasing. NO digits at all unless they appear inside a "
            + "product-model name that already shows up verbatim in the "
            + "input titles. Use 'cheapest', 'lower-end', 'a small "
            + "fraction', 'most listings' instead of any number, "
            + "percentage, or comparison. Plain prose only, no headers."
        )
        resp = _call(retry_system)
        context_text = _strip_context_prefix(resp.text.strip())
        total_input_tokens += resp.input_tokens or 0
        total_output_tokens += resp.output_tokens or 0
        post_check(context_text, payload)

    if not context_text:
        context_text = (
            "_(The synthesizer returned an empty Context paragraph. The "
            "deterministic sections above show the day's data.)_"
        )

    bl_md = build_bottom_line_md(listings, profile)
    listings_md = build_listings_table_md(listings, profile.report_columns)
    diff_md = build_diff_md(diff)
    flags_md = build_flags_md(listings, profile)

    final_report_md = (
        f"{bl_md}\n\n"
        f"{listings_md}\n\n"
        f"{diff_md}\n\n"
        f"{flags_md}\n\n"
        f"**Context.** {context_text}"
    )

    # `input_tokens` / `output_tokens` are SUMS across the initial call and
    # the retry (if any), so the Run cost panel reflects the true synth
    # spend rather than only the surviving call.
    return SynthesisResult(
        report_md=final_report_md,
        provider=provider,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        prompt_chars=len(system_prompt) + len(user_content),
    )
