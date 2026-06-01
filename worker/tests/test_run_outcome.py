"""Honest run-outcome taxonomy (Phase 32, REBUILD_PLAN §8)."""

from __future__ import annotations

from product_search.run_outcome import RunOutcomeClass, classify_run_outcome


def test_ok() -> None:
    o = classify_run_outcome(recall_count=40, survivor_count=12)
    assert o.klass is RunOutcomeClass.OK
    assert o.is_clean
    assert o.message == ""
    assert o.notes == []


def test_no_recall() -> None:
    o = classify_run_outcome(recall_count=0, survivor_count=0)
    assert o.klass is RunOutcomeClass.NO_RECALL
    assert not o.is_clean


def test_index_unavailable_beats_no_recall() -> None:
    o = classify_run_outcome(recall_count=0, survivor_count=0, serper_error=True)
    assert o.klass is RunOutcomeClass.INDEX_UNAVAILABLE


def test_all_filtered() -> None:
    o = classify_run_outcome(recall_count=40, survivor_count=0)
    assert o.klass is RunOutcomeClass.ALL_FILTERED


def test_ebay_note_is_additive_not_headline() -> None:
    o = classify_run_outcome(recall_count=40, survivor_count=5, ebay_error=True)
    assert o.klass is RunOutcomeClass.OK
    assert any(c == "ebay_unavailable" for c, _ in o.notes)


def test_amazon_note_is_additive_not_headline() -> None:
    o = classify_run_outcome(recall_count=40, survivor_count=5, amazon_error=True)
    assert o.klass is RunOutcomeClass.OK
    assert any(c == "amazon_unavailable" for c, _ in o.notes)


def test_degraded_attr_note() -> None:
    o = classify_run_outcome(recall_count=40, survivor_count=5, degraded_attrs=True)
    assert o.klass is RunOutcomeClass.OK
    assert any(c == "degraded_attr" for c, _ in o.notes)


def test_to_dict_shape() -> None:
    o = classify_run_outcome(recall_count=0, survivor_count=0)
    d = o.to_dict()
    assert d["class"] == "no_recall"
    assert isinstance(d["message"], str) and d["message"]
    assert d["notes"] == []
