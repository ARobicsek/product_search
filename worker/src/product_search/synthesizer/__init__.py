"""Synthesizer — turns verified listings into a deterministic markdown report.

Public surface::

    from product_search.synthesizer import synthesize

Per ADR-096 the synth LLM call has been retired — every section of the
report is now built deterministically from verified data. The post-check
machinery (``PostCheckError``, ``post_check``, ``render_prompt``) is
retained on the public surface for backwards-compat imports and future
re-introduction, but is not invoked by ``synthesize()`` at runtime.

The React UI consumes a JSON sidecar emitted by
``product_search.synthesizer.report_json``; the markdown produced here
is the legacy-renderer fallback.
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
