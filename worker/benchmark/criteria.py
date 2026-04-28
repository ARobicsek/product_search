"""The six bar criteria from LLM_STRATEGY.md.

Each criterion takes ``(report_md, payload)`` and returns
``(passed: bool, detail: str)``. ``run_all`` returns a per-criterion
record. The runner aggregates these into a benchmark summary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

import markdown  # type: ignore[import-untyped]

from product_search.synthesizer import PostCheckError, post_check

WORD_LIMIT_CONTEXT = 200


@dataclass
class CriterionResult:
    name: str
    passed: bool
    detail: str


# ---------------------------------------------------------------------------
# Helpers for table parsing
# ---------------------------------------------------------------------------


def _table_lines(report_md: str) -> list[str]:
    """Return the data lines of the first markdown table in *report_md*.

    Excludes the header row and the separator row. A markdown table is
    detected by two consecutive lines starting with ``|`` followed by a
    separator line of dashes.
    """
    lines = report_md.splitlines()
    out: list[str] = []
    in_table = False
    for i, ln in enumerate(lines):
        if not in_table:
            if (
                ln.lstrip().startswith("|")
                and i + 1 < len(lines)
                and re.match(r"^\s*\|?\s*-{3,}", lines[i + 1])
            ):
                in_table = True
                continue  # skip header
        else:
            if not ln.lstrip().startswith("|"):
                break
            if re.match(r"^\s*\|?[\s\-:|]+$", ln):
                continue  # separator row
            out.append(ln)
    return out


def _row_cells(line: str) -> list[str]:
    """Split a markdown table data line into trimmed cell strings."""
    parts = line.strip().strip("|").split("|")
    return [p.strip() for p in parts]


def _extract_floats_from_text(text: str) -> list[float]:
    return [float(m) for m in re.findall(r"\d+(?:\.\d+)?", text)]


# ---------------------------------------------------------------------------
# Criterion 1: no fabricated data
# ---------------------------------------------------------------------------


def check_no_fabrication(report_md: str, payload: dict[str, Any]) -> CriterionResult:
    try:
        post_check(report_md, payload)
    except PostCheckError as exc:
        return CriterionResult("no_fabrication", False, str(exc))
    return CriterionResult("no_fabrication", True, "OK")


# ---------------------------------------------------------------------------
# Criterion 2: every input row appears
# ---------------------------------------------------------------------------


def check_all_rows_present(report_md: str, payload: dict[str, Any]) -> CriterionResult:
    listings = cast(list[dict[str, Any]], payload.get("listings") or [])
    missing = [
        cast(str, lst["url"])
        for lst in listings
        if cast(str, lst["url"]) not in report_md
    ]
    if missing:
        return CriterionResult(
            "all_rows_present",
            False,
            f"{len(missing)} of {len(listings)} URLs missing: {missing[:3]}",
        )
    return CriterionResult(
        "all_rows_present", True, f"all {len(listings)} URLs present"
    )


# ---------------------------------------------------------------------------
# Criterion 3: ranked by total_for_target_usd ascending
# ---------------------------------------------------------------------------


def check_sort_order(report_md: str, payload: dict[str, Any]) -> CriterionResult:
    """Walk the table data rows in order. For each row, find which input
    listing it is by URL substring, and confirm the total_for_target_usd
    sequence is non-decreasing (treating null as +inf — null rows go last).
    """
    listings_by_url: dict[str, dict[str, Any]] = {
        cast(str, lst["url"]): lst
        for lst in cast(list[dict[str, Any]], payload.get("listings") or [])
    }
    rows = _table_lines(report_md)
    if not rows:
        return CriterionResult("sort_order", False, "no table rows found")

    seen_totals: list[float] = []
    for ln in rows:
        cells = " ".join(_row_cells(ln))
        matched = next(
            (lst for url, lst in listings_by_url.items() if url in cells),
            None,
        )
        if matched is None:
            # Row not tied to any input listing; can't validate sort order.
            return CriterionResult(
                "sort_order", False, f"row not tied to any input URL: {ln[:80]!r}"
            )
        tot = matched["total_for_target_usd"]
        seen_totals.append(float("inf") if tot is None else float(tot))

    for i in range(1, len(seen_totals)):
        if seen_totals[i] < seen_totals[i - 1]:
            return CriterionResult(
                "sort_order",
                False,
                f"row {i} total {seen_totals[i]} < row {i - 1} total {seen_totals[i - 1]}",
            )
    return CriterionResult("sort_order", True, f"{len(seen_totals)} rows in order")


# ---------------------------------------------------------------------------
# Criterion 4: every flag is surfaced
# ---------------------------------------------------------------------------


def check_flags_surfaced(report_md: str, payload: dict[str, Any]) -> CriterionResult:
    listings = cast(list[dict[str, Any]], payload.get("listings") or [])
    flags: set[str] = set()
    for lst in listings:
        flags.update(cast(list[str], lst.get("flags") or []))

    if not flags:
        return CriterionResult("flags_surfaced", True, "no flags in input")

    lower = report_md.lower()
    missing = []
    for flag in flags:
        # Allow either the raw flag name (e.g. "china_shipping") OR a
        # plain-English rendering containing each underscore-separated
        # word (e.g. "China shipping").
        if flag.lower() in lower:
            continue
        words = flag.lower().split("_")
        if all(w in lower for w in words if w):
            continue
        missing.append(flag)

    if missing:
        return CriterionResult(
            "flags_surfaced", False, f"flags not surfaced: {missing}"
        )
    return CriterionResult(
        "flags_surfaced", True, f"all {len(flags)} flag(s) surfaced"
    )


# ---------------------------------------------------------------------------
# Criterion 5: <=200 words of context narrative
# ---------------------------------------------------------------------------


def _extract_context_section(report_md: str) -> str:
    """Return the body of the 'Context' section.

    Tolerates several header styles: ``## Context``, ``**Context.**``,
    ``5. **Context.**``. The body may live on the same line as the header
    (e.g. ``**Context.** All three listings are…``).
    """
    lines = report_md.splitlines()
    capture = False
    out: list[str] = []
    header_re = re.compile(
        r"^\s*(?:[\-\*]\s*)?(?:\d+\.\s*)?(?:#{1,6}\s*)?\**\s*context\s*[\.\:\*]*\s*",
        re.IGNORECASE,
    )
    next_header_re = re.compile(
        r"^(#{1,6}\s)|^\*\*[A-Z]|^[\-\*]\s+\*\*[A-Z]"
    )
    for ln in lines:
        if not capture:
            m = header_re.match(ln)
            if m and "context" in ln.lower():
                capture = True
                rest = ln[m.end():]
                if rest.strip():
                    out.append(rest)
                continue
        else:
            if next_header_re.match(ln.strip()):
                break
            out.append(ln)
    return "\n".join(out).strip()


def check_context_length(report_md: str, payload: dict[str, Any]) -> CriterionResult:
    section = _extract_context_section(report_md)
    if not section:
        return CriterionResult(
            "context_length", False, "context section not found"
        )
    words = re.findall(r"\b[\w'-]+\b", section)
    if len(words) > WORD_LIMIT_CONTEXT:
        return CriterionResult(
            "context_length",
            False,
            f"context section is {len(words)} words; cap is {WORD_LIMIT_CONTEXT}",
        )
    return CriterionResult(
        "context_length", True, f"{len(words)} words"
    )


# ---------------------------------------------------------------------------
# Criterion 6: markdown renders
# ---------------------------------------------------------------------------


def check_markdown_renders(
    report_md: str, payload: dict[str, Any]
) -> CriterionResult:
    try:
        html = markdown.markdown(report_md, extensions=["tables"])
    except Exception as exc:  # noqa: BLE001 — markdown libs raise heterogeneously
        return CriterionResult("markdown_renders", False, f"render failed: {exc}")
    if not html.strip():
        return CriterionResult(
            "markdown_renders", False, "rendered HTML is empty"
        )
    return CriterionResult(
        "markdown_renders", True, f"{len(html)} chars of HTML"
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


ALL_CRITERIA = [
    check_no_fabrication,
    check_all_rows_present,
    check_sort_order,
    check_flags_surfaced,
    check_context_length,
    check_markdown_renders,
]


def run_all(report_md: str, payload: dict[str, Any]) -> list[CriterionResult]:
    return [check(report_md, payload) for check in ALL_CRITERIA]
