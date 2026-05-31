"""Honest run-outcome taxonomy for the v2 (Serper-recall) pipeline.

Recall via a shopping index collapses the old per-vendor scraping diagnostics
into a small, clear set of run-level outcomes (REBUILD_PLAN §8). Each maps to a
single actionable, plain-English message. The rich per-listing ai_filter log
still backs ``all_filtered``.

Deterministic; no LLM. The classifier takes only counts + error booleans, so it
is trivially testable and never fabricates a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RunOutcomeClass(str, Enum):
    OK = "ok"
    INDEX_UNAVAILABLE = "index_unavailable"
    NO_RECALL = "no_recall"
    ALL_FILTERED = "all_filtered"


# Additive notes that can accompany any primary class (they don't, on their own,
# describe the whole run — e.g. eBay failing while Serper succeeded).
class RunOutcomeNote(str, Enum):
    EBAY_UNAVAILABLE = "ebay_unavailable"
    DEGRADED_ATTR = "degraded_attr"


_PRIMARY_MESSAGES: dict[RunOutcomeClass, str] = {
    RunOutcomeClass.OK: "",
    RunOutcomeClass.INDEX_UNAVAILABLE: (
        "Couldn't reach the shopping index this run; the next scheduled run "
        "retries automatically."
    ),
    RunOutcomeClass.NO_RECALL: (
        "No offers found — the query may be too specific, or this item isn't "
        "sold online. Edit the search query."
    ),
    RunOutcomeClass.ALL_FILTERED: (
        "Found offers but none matched your spec — your match/filters may be too "
        "strict, or the query is mis-scoped. See the filter diagnostics."
    ),
}

_NOTE_MESSAGES: dict[RunOutcomeNote, str] = {
    RunOutcomeNote.EBAY_UNAVAILABLE: (
        "eBay was unavailable this run; other sources still ran."
    ),
    RunOutcomeNote.DEGRADED_ATTR: (
        "Stock count isn't available from the shopping index; showing offers "
        "without quantity verification."
    ),
}


@dataclass
class RunOutcome:
    klass: RunOutcomeClass
    message: str
    notes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.klass is RunOutcomeClass.OK

    def to_dict(self) -> dict[str, object]:
        return {
            "class": self.klass.value,
            "message": self.message,
            "notes": [{"class": c, "message": m} for c, m in self.notes],
        }


def classify_run_outcome(
    *,
    recall_count: int,
    survivor_count: int,
    serper_error: bool = False,
    ebay_error: bool = False,
    degraded_attrs: bool = False,
) -> RunOutcome:
    """Classify a v2 run into one primary outcome + any additive notes.

    Primary precedence: index unavailable (the recall layer itself failed) →
    no recall (0 offers) → all filtered (offers but 0 survivors) → ok. eBay
    failure and degraded attributes are additive notes, never the headline,
    because Serper alone can still produce a good run.
    """
    notes: list[tuple[str, str]] = []
    if ebay_error:
        notes.append(
            (RunOutcomeNote.EBAY_UNAVAILABLE.value, _NOTE_MESSAGES[RunOutcomeNote.EBAY_UNAVAILABLE])
        )
    if degraded_attrs:
        notes.append(
            (RunOutcomeNote.DEGRADED_ATTR.value, _NOTE_MESSAGES[RunOutcomeNote.DEGRADED_ATTR])
        )

    if serper_error and recall_count == 0:
        klass = RunOutcomeClass.INDEX_UNAVAILABLE
    elif recall_count == 0:
        klass = RunOutcomeClass.NO_RECALL
    elif survivor_count == 0:
        klass = RunOutcomeClass.ALL_FILTERED
    else:
        klass = RunOutcomeClass.OK

    return RunOutcome(klass=klass, message=_PRIMARY_MESSAGES[klass], notes=notes)
