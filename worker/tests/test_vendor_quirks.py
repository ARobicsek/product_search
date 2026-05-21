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
