"""Sanity checks for the benchmark criteria.

These verify that the criteria functions correctly accept good reports
and flag bad ones — without ever calling an LLM. The full benchmark
runs separately via ``python -m benchmark.runner``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Make ``benchmark`` (a sibling of ``src/``) importable from tests.
_BENCH_PARENT = Path(__file__).parent.parent
if str(_BENCH_PARENT) not in sys.path:
    sys.path.insert(0, str(_BENCH_PARENT))

from benchmark.criteria import (  # noqa: E402
    check_all_rows_present,
    check_context_length,
    check_flags_surfaced,
    check_markdown_renders,
    check_no_fabrication,
    check_sort_order,
)


def _payload_two_listings_one_flag() -> dict[str, Any]:
    fixture = (
        Path(__file__).parent.parent / "benchmark" / "fixtures" / "03_flags.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


def _good_report(payload: dict[str, Any]) -> str:
    listings = payload["listings"]
    rows = []
    for i, lst in enumerate(listings, start=1):
        rows.append(
            f"| {i} | {lst['source']} | {lst['url']} | "
            f"{lst['unit_price_usd']} | {lst['total_for_target_usd']} | "
            f"{', '.join(lst.get('flags') or [])} |"
        )
    table = (
        "| Rank | Source | URL | Price | Total | Flags |\n"
        "|---|---|---|---|---|---|\n" + "\n".join(rows)
    )
    flags = sorted({f for lst in listings for f in lst.get("flags", [])})
    flag_lines = "\n".join(f"- {f}: explanation" for f in flags) or "(no flags)"
    return (
        f"**Bottom line.** cheapest path is {listings[0]['url']}.\n\n"
        f"## Ranked listings\n\n{table}\n\n"
        f"## Diff vs yesterday\n- New: (none)\n- Dropped: (none)\n- Changed: (none)\n\n"
        f"## Flags\n{flag_lines}\n\n"
        f"## Context\nA short note about the data.\n"
    )


def test_criteria_pass_on_clean_report() -> None:
    payload = _payload_two_listings_one_flag()
    report = _good_report(payload)
    assert check_no_fabrication(report, payload).passed
    assert check_all_rows_present(report, payload).passed
    assert check_sort_order(report, payload).passed
    assert check_flags_surfaced(report, payload).passed
    assert check_context_length(report, payload).passed
    assert check_markdown_renders(report, payload).passed


def test_all_rows_present_flags_missing_url() -> None:
    payload = _payload_two_listings_one_flag()
    # Drop one URL from the report
    truncated_report = _good_report(payload).replace(
        payload["listings"][1]["url"], "REDACTED"
    )
    assert not check_all_rows_present(truncated_report, payload).passed


def test_sort_order_flags_inverted_rows() -> None:
    """If we manually reverse the table, sort_order should detect it."""
    payload = _payload_two_listings_one_flag()
    listings = list(payload["listings"])
    # Reverse the table so the most expensive total comes first
    rows_reversed = list(reversed(listings))
    table_lines = "\n".join(
        f"| {i + 1} | {lst['url']} | {lst['total_for_target_usd']} |"
        for i, lst in enumerate(rows_reversed)
    )
    report = (
        "## Ranked\n\n"
        "| Rank | URL | Total |\n|---|---|---|\n" + table_lines + "\n\n"
        "## Context\nshort note.\n"
    )
    res = check_sort_order(report, payload)
    # The fixture's listings array is already sorted asc by build_input_payload.
    # Reversing them means total_for_target_usd is descending — must fail.
    if listings[0]["total_for_target_usd"] != listings[-1]["total_for_target_usd"]:
        assert not res.passed


def test_context_length_flags_overlong_section() -> None:
    payload = _payload_two_listings_one_flag()
    long_section = " ".join(["word"] * 250)
    report = f"## Context\n{long_section}\n"
    res = check_context_length(report, payload)
    assert not res.passed
    assert "200" in res.detail


def test_flags_surfaced_accepts_plain_english() -> None:
    payload = _payload_two_listings_one_flag()
    # Use plain-English wording instead of underscored flag names.
    report = (
        "Mention shipping from China and a smart memory branded module "
        "and one with low seller feedback."
    )
    res = check_flags_surfaced(report, payload)
    assert res.passed, res.detail


def test_no_fabrication_catches_invented_price() -> None:
    payload = _payload_two_listings_one_flag()
    report = "Best price is $77.77 per module."
    assert not check_no_fabrication(report, payload).passed
