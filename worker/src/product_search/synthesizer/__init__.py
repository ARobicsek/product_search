"""Synthesizer — turns verified listings + diff into a markdown report.

Public surface::

    from product_search.synthesizer import synthesize, PostCheckError

The post-check enforces ADR-001: the LLM never produces a price, URL,
MPN, or stock count that wasn't in its input. If it does, the run fails
loudly rather than committing fabricated data.
"""

from product_search.synthesizer.report import default_report_path, write_report
from product_search.synthesizer.synthesizer import (
    COLUMN_DEFS,
    DEFAULT_REPORT_COLUMNS,
    FLAG_FALLBACK_DESCRIPTIONS,
    SYNTH_MAX_LISTINGS,
    PostCheckError,
    SynthesisResult,
    build_bottom_line_md,
    build_diff_md,
    build_flags_md,
    build_input_payload,
    build_listings_table_md,
    post_check,
    render_prompt,
    synthesize,
)

__all__ = [
    "COLUMN_DEFS",
    "DEFAULT_REPORT_COLUMNS",
    "FLAG_FALLBACK_DESCRIPTIONS",
    "SYNTH_MAX_LISTINGS",
    "PostCheckError",
    "SynthesisResult",
    "build_bottom_line_md",
    "build_diff_md",
    "build_flags_md",
    "build_input_payload",
    "build_listings_table_md",
    "default_report_path",
    "post_check",
    "render_prompt",
    "synthesize",
    "write_report",
]
