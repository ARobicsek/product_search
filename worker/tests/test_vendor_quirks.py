"""Tests for the vendor quirks registry loader (ADR-068)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from product_search import vendor_quirks


@pytest.fixture
def registry_yaml(tmp_path: Path) -> Path:
    """Write a small registry to a temp file and point the loader at it."""
    yaml_path = tmp_path / "vendor_quirks.yaml"
    yaml_path.write_text(
        dedent(
            """
            bestbuy.com:
              default_alterlab_options:
                country: us
                min_tier: 3
              url_transforms:
                - when:
                    path_prefix: /site/searchpage.jsp
                  append_query:
                    intl: nosplash
                  reason: skip splash
              force_detail_backup: true
              alterlab_known_good: true

            target.com:
              default_alterlab_options:
                country: us
                min_tier: 3
              force_detail_backup: true

            example.com:
              alterlab_known_good: true
            """
        ).lstrip(),
        encoding="utf-8",
    )
    vendor_quirks._load_registry.cache_clear()
    yield yaml_path
    vendor_quirks._load_registry.cache_clear()


def _reg(path: Path) -> dict:
    return vendor_quirks._load_registry(str(path))


def test_host_normalization_strips_www_and_lowercases(registry_yaml: Path):
    reg = _reg(registry_yaml)
    assert "bestbuy.com" in reg
    # _normalize_host
    assert vendor_quirks._normalize_host("WWW.BESTBUY.COM") == "bestbuy.com"
    assert vendor_quirks._normalize_host("bestbuy.com") == "bestbuy.com"
    assert vendor_quirks._normalize_host("") == ""


def test_get_quirks_for_url_resolves_host(registry_yaml: Path, monkeypatch):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    q = vendor_quirks.get_quirks_for_url(
        "https://www.bestbuy.com/site/searchpage.jsp?st=foo"
    )
    assert q.get("force_detail_backup") is True
    assert q["default_alterlab_options"]["min_tier"] == 3


def test_merge_alterlab_options_source_wins(registry_yaml: Path, monkeypatch):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()

    # Source overrides default min_tier; preserves country from defaults.
    merged = vendor_quirks.merge_alterlab_options(
        "https://bestbuy.com/x", {"min_tier": 4}
    )
    assert merged == {"country": "us", "min_tier": 4}


def test_merge_alterlab_options_returns_none_when_empty(
    registry_yaml: Path, monkeypatch
):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    # No defaults for unknown host, no source-level options.
    assert vendor_quirks.merge_alterlab_options("https://random.example/x", None) is None


def test_merge_alterlab_options_uses_defaults_when_no_source(
    registry_yaml: Path, monkeypatch
):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    merged = vendor_quirks.merge_alterlab_options("https://target.com/s/x", None)
    assert merged == {"country": "us", "min_tier": 3}


def test_apply_url_transforms_appends_query_when_missing(
    registry_yaml: Path, monkeypatch
):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    new_url, applied = vendor_quirks.apply_url_transforms(
        "https://www.bestbuy.com/site/searchpage.jsp?st=wh-1000xm5"
    )
    assert "intl=nosplash" in new_url
    assert applied == ["bestbuy.com.append_query[0]"]


def test_apply_url_transforms_does_not_duplicate_existing_param(
    registry_yaml: Path, monkeypatch
):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    # URL already has intl=nosplash → no change, no applied entry.
    original = "https://www.bestbuy.com/site/searchpage.jsp?st=x&intl=nosplash"
    new_url, applied = vendor_quirks.apply_url_transforms(original)
    assert applied == []
    assert new_url == original


def test_apply_url_transforms_skips_when_path_prefix_does_not_match(
    registry_yaml: Path, monkeypatch
):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    # Detail page, not /site/searchpage.jsp — transform must NOT fire.
    new_url, applied = vendor_quirks.apply_url_transforms(
        "https://www.bestbuy.com/site/sony-headphones/12345.p?skuId=12345"
    )
    assert applied == []
    assert "intl=nosplash" not in new_url


def test_apply_url_transforms_noop_for_unknown_host(
    registry_yaml: Path, monkeypatch
):
    monkeypatch.setattr(vendor_quirks, "_default_registry_path", lambda: registry_yaml)
    vendor_quirks._load_registry.cache_clear()
    new_url, applied = vendor_quirks.apply_url_transforms(
        "https://unknown-vendor.example/products/x"
    )
    assert applied == []
    assert new_url == "https://unknown-vendor.example/products/x"


def test_missing_registry_returns_empty_and_does_not_raise(tmp_path: Path):
    missing = tmp_path / "nope.yaml"
    vendor_quirks._load_registry.cache_clear()
    reg = vendor_quirks._load_registry(str(missing))
    assert reg == {}


def test_committed_registry_is_loadable_and_has_seed_entries():
    """Sanity: the actual committed YAML parses and has the expected hosts."""
    vendor_quirks._load_registry.cache_clear()
    reg = vendor_quirks._load_registry()
    # Seed entries from worker/data/vendor_quirks.yaml — names matter
    # because the adapter looks them up by host.
    for host in ("bestbuy.com", "target.com", "microcenter.com", "bhphotovideo.com"):
        assert host in reg, f"{host} missing from committed vendor_quirks.yaml"
    assert reg["bestbuy.com"]["force_detail_backup"] is True
    # The known nosplash transform must be present — this is the regression
    # guard for the e93fd47 fix that originally got lost.
    transforms = reg["bestbuy.com"]["url_transforms"]
    assert any(
        isinstance(t, dict)
        and t.get("append_query", {}).get("intl") == "nosplash"
        for t in transforms
    ), "bestbuy.com nosplash transform missing — see ADR-068"


# ---------------------------------------------------------------------------
# Phase 24 / ADR-082: Amazon + Backmarket defaults, Adorama bare-path,
# and the registry-load consistency check.
# ---------------------------------------------------------------------------


def test_amazon_default_options_merge_through_committed_registry():
    """Phase 24: amazon.com search URLs must auto-get
    `country: us, min_tier: 3, wait_condition: networkidle` defaults.

    Without these, Amazon's JS-rendered tiles aren't present in the captured
    HTML and recall drops to 0 (Phase 23 Part A, commit a1f98dc).
    """
    vendor_quirks._load_registry.cache_clear()
    merged = vendor_quirks.merge_alterlab_options(
        "https://www.amazon.com/s?k=logitech+mx+master+3s", None
    )
    assert merged == {
        "country": "us",
        "min_tier": 3,
        "wait_condition": "networkidle",
    }


def test_amazon_source_options_override_defaults():
    """Source-level explicit options win over registry defaults — but the
    other defaults survive the merge so a partial override doesn't strip
    the rest of the vendor knowledge."""
    vendor_quirks._load_registry.cache_clear()
    merged = vendor_quirks.merge_alterlab_options(
        "https://www.amazon.com/dp/B09HM94VDS", {"min_tier": 4}
    )
    assert merged == {
        "country": "us",
        "min_tier": 4,
        "wait_condition": "networkidle",
    }


def test_backmarket_default_options_merge_through_committed_registry():
    """Phase 24: backmarket.com search URLs must auto-get the same JS-render
    defaults as Amazon — bare-path fetch returns ~900 KB of nav chrome with
    0 JSON-LD listings, so without `wait_condition: networkidle` the runtime
    can't see products."""
    vendor_quirks._load_registry.cache_clear()
    merged = vendor_quirks.merge_alterlab_options(
        "https://www.backmarket.com/en-us/search?q=logitech+mx+master+3s", None
    )
    assert merged == {
        "country": "us",
        "min_tier": 3,
        "wait_condition": "networkidle",
    }


def test_adorama_has_no_default_alterlab_options():
    """Phase 24 / ADR-082: adorama.com's bare path (curl_cffi fallback)
    already returns 23 JSON-LD products on the live probe — adding AlterLab
    defaults would burn cost for no recall gain. Pin: no defaults.
    """
    vendor_quirks._load_registry.cache_clear()
    quirks = vendor_quirks.get_quirks_for_url(
        "https://www.adorama.com/l/?searchinfo=logitech+mx+master+3s"
    )
    assert quirks.get("default_alterlab_options") in (None, {})


@pytest.fixture
def caplog_registry(tmp_path: Path) -> Path:
    """A registry with one inconsistent host and one consistent host, for
    exercising the ADR-082 consistency check.
    """
    p = tmp_path / "registry_for_caplog.yaml"
    p.write_text(
        dedent(
            """
            badhost.example:
              alterlab_known_good: true
            goodhost.example:
              alterlab_known_good: true
              default_alterlab_options:
                wait_condition: networkidle
            nonefacet.example:
              force_detail_backup: true
            """
        ).lstrip(),
        encoding="utf-8",
    )
    vendor_quirks._load_registry.cache_clear()
    yield p
    vendor_quirks._load_registry.cache_clear()


def test_consistency_check_warns_on_alterlab_known_good_without_defaults(
    caplog_registry: Path, caplog
):
    """ADR-082: a host marked `alterlab_known_good: true` without
    `default_alterlab_options` triggers a WARNING at registry load (the
    Phase 23 Part A Amazon silent-fail class).
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="product_search.vendor_quirks"):
        _reg(caplog_registry)

    warning_text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "badhost.example" in warning_text
    assert "ADR-082" in warning_text
    # The consistent host must NOT warn.
    assert "goodhost.example" not in warning_text
    # A host without `alterlab_known_good` must NOT warn even when missing
    # defaults — the check is targeted, not "warn on every missing field".
    assert "nonefacet.example" not in warning_text


def test_consistency_check_silent_on_well_formed_registry(tmp_path: Path, caplog):
    """Negative case: a registry where every `alterlab_known_good` host also
    has `default_alterlab_options` produces NO warning. The check only fires
    on the actual gap class.
    """
    import logging

    p = tmp_path / "ok_registry.yaml"
    p.write_text(
        dedent(
            """
            ok.example:
              alterlab_known_good: true
              default_alterlab_options:
                wait_condition: networkidle
            no_flag.example:
              force_detail_backup: true
            """
        ).lstrip(),
        encoding="utf-8",
    )
    vendor_quirks._load_registry.cache_clear()
    with caplog.at_level(logging.WARNING, logger="product_search.vendor_quirks"):
        vendor_quirks._load_registry(str(p))
    vendor_quirks._load_registry.cache_clear()

    assert not [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "ADR-082" in r.getMessage()
    ]
