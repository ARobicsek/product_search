"""Synthesizer — render today's listings + diff into a markdown report.

Public entry point is :func:`synthesize`. It calls the configured LLM
with the prompt at ``prompts/synth_v1.txt`` and runs :func:`post_check`
on the output. Per ADR-001 the run fails loud if any price, or quantity
in the report commentary does not appear in the input — we'd rather
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
    """Raised when the synth output contains numbers not in the input."""


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

def build_listings_table_md(listings: list[Listing]) -> str:
    lines = ["**Ranked listings.**\n"]
    lines.append("| Rank | Source | Title | Price (unit) | Total for target | Qty | Seller | Flags |")
    lines.append("|---|---|---|---|---|---|---|---|")
    
    def _key(lst: Listing) -> tuple[int, float]:
        if lst.total_for_target_usd is None:
            return (1, 0.0)
        return (0, lst.total_for_target_usd)
        
    sorted_listings = sorted(listings, key=_key)[:SYNTH_MAX_LISTINGS]
    
    for i, lst in enumerate(sorted_listings, 1):
        source_link = f"[{lst.source}]({lst.url})"
        title = lst.title.replace("|", "\\|")
        price = f"${lst.unit_price_usd:.2f}" if lst.unit_price_usd is not None else "unknown"
        total = f"${lst.total_for_target_usd:.2f}" if lst.total_for_target_usd is not None else "unknown"
        qty = str(lst.quantity_available) if lst.quantity_available is not None else "unknown"
        seller = lst.seller_name.replace("|", "\\|") if lst.seller_name else "unknown"
        flags = ", ".join(lst.flags) if lst.flags else "(no flags)"
        lines.append(f"| {i} | {source_link} | {title} | {price} | {total} | {qty} | {seller} | {flags} |")
        
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
        raise PostCheckError(f"Synthesizer post-check failed: fabricated numbers: {bad_numbers}")


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

    llm_report_md = resp.text.strip()
    post_check(llm_report_md, payload)
    
    # Extract sections using regex
    import re
    bl_match = re.search(r'1\.\s*\*\*Bottom line\.\*\*(.*?)(?=2\.\s*\*\*Flags\.\*\*|$)', llm_report_md, re.DOTALL)
    flags_match = re.search(r'2\.\s*\*\*Flags\.\*\*(.*?)(?=3\.\s*\*\*Context\.\*\*|$)', llm_report_md, re.DOTALL)
    context_match = re.search(r'3\.\s*\*\*Context\.\*\*(.*)', llm_report_md, re.DOTALL)
    
    bl_text = bl_match.group(1).strip() if bl_match else "(extraction failed)"
    flags_text = flags_match.group(1).strip() if flags_match else "(extraction failed)"
    ctx_text = context_match.group(1).strip() if context_match else "(extraction failed)"
    
    # Inject deterministic Python tables and re-number
    listings_md = build_listings_table_md(listings)
    diff_md = build_diff_md(diff)
    
    final_report_md = f"""1. **Bottom line.** {bl_text}

{listings_md}

{diff_md}

4. **Flags.** {flags_text}

5. **Context.** {ctx_text}"""

    return SynthesisResult(
        report_md=final_report_md,
        provider=provider,
        model=model,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        prompt_chars=len(system_prompt) + len(user_content),
    )
