"""Generate the >=10 benchmark fixture payloads.

Each fixture is a JSON file under ``worker/benchmark/fixtures/``. The
shape matches what :func:`product_search.synthesizer.build_input_payload`
produces, so the runner can hand the same shape to the LLM that the
production code does.

Run with:  ``python -m benchmark.fixture_gen``
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from product_search.models import Listing
from product_search.profile import load_profile
from product_search.storage.diff import DiffResult, PriceChange
from product_search.synthesizer import build_input_payload

PROFILE_SLUG = "ddr5-rdimm-256gb"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _listing(
    *,
    url: str,
    title: str,
    unit_price_usd: float,
    capacity_gb: int,
    speed_mts: int = 4800,
    is_kit: bool = False,
    kit_module_count: int = 1,
    kit_price_usd: float | None = None,
    quantity_available: int | None = 10,
    seller_name: str = "techseller",
    seller_rating_pct: float | None = 99.5,
    seller_feedback_count: int | None = 5000,
    ship_from_country: str = "US",
    flags: list[str] | None = None,
    qvl_status: str | None = "qvl",
    brand: str = "Samsung",
    mpn: str | None = "M321R4GA0BB0-CQK",
    total_for_target_usd: float | None = None,
) -> Listing:
    return Listing(
        source="ebay_search",
        url=url,
        title=title,
        fetched_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        brand=brand,
        mpn=mpn,
        attrs={
            "capacity_gb": capacity_gb,
            "speed_mts": speed_mts,
            "form_factor": "RDIMM",
            "ecc": True,
        },
        condition="new",
        is_kit=is_kit,
        kit_module_count=kit_module_count,
        unit_price_usd=unit_price_usd,
        kit_price_usd=kit_price_usd,
        quantity_available=quantity_available,
        seller_name=seller_name,
        seller_rating_pct=seller_rating_pct,
        seller_feedback_count=seller_feedback_count,
        ship_from_country=ship_from_country,
        qvl_status=qvl_status,
        flags=flags or [],
        total_for_target_usd=total_for_target_usd,
    )


def _scenarios() -> dict[str, tuple[list[Listing], DiffResult | None]]:
    """Return ``{name: (listings, diff)}`` for each fixture."""
    out: dict[str, tuple[list[Listing], DiffResult | None]] = {}

    # 01 — small all-pass: 3 viable 32GB listings, no diff (first run)
    out["01_small_no_diff"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/100",
                title="Samsung 32GB DDR5 4800 RDIMM ECC",
                unit_price_usd=120.0,
                capacity_gb=32,
                total_for_target_usd=960.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/101",
                title="Hynix 32GB DDR5 4800 RDIMM ECC",
                unit_price_usd=125.5,
                capacity_gb=32,
                brand="Hynix",
                mpn="HMCG88AGBRA174N",
                total_for_target_usd=1004.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/102",
                title="Micron 32GB DDR5 4800 RDIMM ECC",
                unit_price_usd=130.0,
                capacity_gb=32,
                brand="Micron",
                mpn="MTC20F2046S1RC48BA1",
                total_for_target_usd=1040.0,
            ),
        ],
        None,
    )

    # 02 — mixed capacities (32 and 64), with one null total
    out["02_mixed_capacity"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/200",
                title="Samsung 32GB DDR5 4800 RDIMM",
                unit_price_usd=115.0,
                capacity_gb=32,
                total_for_target_usd=920.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/201",
                title="Samsung 64GB DDR5 4800 RDIMM",
                unit_price_usd=240.0,
                capacity_gb=64,
                total_for_target_usd=960.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/202",
                title="48GB DDR5 4800 RDIMM (off-spec)",
                unit_price_usd=180.0,
                capacity_gb=48,
                total_for_target_usd=None,
            ),
        ],
        DiffResult(),  # empty diff
    )

    # 03 — flags: china_shipping, smart_memory, low_seller_feedback
    out["03_flags"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/300",
                title="Samsung 32GB DDR5 4800 RDIMM ECC",
                unit_price_usd=110.0,
                capacity_gb=32,
                ship_from_country="CN",
                flags=["china_shipping"],
                total_for_target_usd=880.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/301",
                title="HPE 32GB DDR5 4800 RDIMM Smart Memory",
                unit_price_usd=145.0,
                capacity_gb=32,
                brand="HPE",
                mpn="P50313-B21",
                flags=["smart_memory"],
                total_for_target_usd=1160.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/302",
                title="32GB DDR5 4800 RDIMM ECC",
                unit_price_usd=99.0,
                capacity_gb=32,
                seller_name="newbie_99",
                seller_rating_pct=97.0,
                seller_feedback_count=120,
                flags=["low_seller_feedback"],
                total_for_target_usd=792.0,
            ),
        ],
        None,
    )

    # 04 — diff with new+dropped+changed
    base_today = [
        _listing(
            url="https://www.ebay.com/itm/400",
            title="Samsung 32GB DDR5 RDIMM",
            unit_price_usd=118.0,
            capacity_gb=32,
            total_for_target_usd=944.0,
        ),
        _listing(
            url="https://www.ebay.com/itm/401",
            title="Hynix 32GB DDR5 RDIMM",
            unit_price_usd=128.0,
            capacity_gb=32,
            brand="Hynix",
            mpn="HMCG88AGBRA174N",
            total_for_target_usd=1024.0,
        ),
        _listing(
            url="https://www.ebay.com/itm/402",
            title="Micron 64GB DDR5 RDIMM",
            unit_price_usd=235.0,
            capacity_gb=64,
            brand="Micron",
            mpn="MTC40F2046S1RC48BA1",
            total_for_target_usd=940.0,
        ),
    ]
    out["04_diff_full"] = (
        base_today,
        DiffResult(
            new=[base_today[2]],
            dropped=[
                _listing(
                    url="https://www.ebay.com/itm/499",
                    title="OLD listing dropped today",
                    unit_price_usd=130.0,
                    capacity_gb=32,
                    total_for_target_usd=1040.0,
                ),
            ],
            changed=[
                PriceChange(
                    url="https://www.ebay.com/itm/400",
                    title="Samsung 32GB DDR5 RDIMM",
                    old_price_usd=125.0,
                    new_price_usd=118.0,
                    pct_change=-0.056,
                    new_listing=base_today[0],
                ),
            ],
        ),
    )

    # 05 — kit listing (8x32GB kit) plus single-module options
    out["05_kit_and_single"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/500",
                title="Kit of 8 Samsung 32GB DDR5 4800 RDIMM",
                unit_price_usd=118.0,
                capacity_gb=32,
                is_kit=True,
                kit_module_count=8,
                kit_price_usd=944.0,
                total_for_target_usd=944.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/501",
                title="Samsung 32GB DDR5 4800 RDIMM (single)",
                unit_price_usd=120.0,
                capacity_gb=32,
                total_for_target_usd=960.0,
            ),
        ],
        None,
    )

    # 06 — all listings have null total (e.g., capacities don't match a config)
    out["06_all_null_totals"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/600",
                title="48GB DDR5 RDIMM (off-spec capacity)",
                unit_price_usd=200.0,
                capacity_gb=48,
                total_for_target_usd=None,
            ),
            _listing(
                url="https://www.ebay.com/itm/601",
                title="96GB DDR5 RDIMM (off-spec capacity)",
                unit_price_usd=420.0,
                capacity_gb=96,
                total_for_target_usd=None,
            ),
        ],
        None,
    )

    # 07 — eight viable 32GB listings (volume scenario)
    seven = []
    for i in range(8):
        price = 110.0 + i * 4.5  # 110, 114.5, 119, ...
        seven.append(
            _listing(
                url=f"https://www.ebay.com/itm/70{i}",
                title=f"Vendor{i} 32GB DDR5 4800 RDIMM",
                unit_price_usd=round(price, 2),
                capacity_gb=32,
                seller_name=f"seller_{i}",
                total_for_target_usd=round(price * 8, 2),
            )
        )
    out["07_volume_eight"] = (seven, DiffResult())

    # 08 — single listing only
    out["08_single_listing"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/800",
                title="Samsung 32GB DDR5 4800 RDIMM ECC",
                unit_price_usd=119.99,
                capacity_gb=32,
                total_for_target_usd=959.92,
            ),
        ],
        None,
    )

    # 09 — multiple flags on one listing (compound flag handling)
    out["09_multi_flags"] = (
        [
            _listing(
                url="https://www.ebay.com/itm/900",
                title="HPE SmartMemory 32GB DDR5 RDIMM",
                unit_price_usd=99.0,
                capacity_gb=32,
                ship_from_country="CN",
                brand="HPE",
                mpn="P50313-B21",
                seller_name="cheap_china",
                seller_rating_pct=97.0,
                seller_feedback_count=200,
                flags=["smart_memory", "china_shipping", "low_seller_feedback"],
                total_for_target_usd=792.0,
            ),
            _listing(
                url="https://www.ebay.com/itm/901",
                title="Samsung 32GB DDR5 4800 RDIMM",
                unit_price_usd=125.0,
                capacity_gb=32,
                total_for_target_usd=1000.0,
            ),
        ],
        None,
    )

    # 10 — diff with only "changed" entries (no new, no dropped)
    today10 = [
        _listing(
            url="https://www.ebay.com/itm/1000",
            title="Samsung 32GB DDR5 RDIMM",
            unit_price_usd=110.0,
            capacity_gb=32,
            total_for_target_usd=880.0,
        ),
        _listing(
            url="https://www.ebay.com/itm/1001",
            title="Hynix 64GB DDR5 RDIMM",
            unit_price_usd=220.0,
            capacity_gb=64,
            brand="Hynix",
            mpn="HMCG88AGBRA174N",
            total_for_target_usd=880.0,
        ),
    ]
    out["10_changes_only"] = (
        today10,
        DiffResult(
            new=[],
            dropped=[],
            changed=[
                PriceChange(
                    url="https://www.ebay.com/itm/1000",
                    title="Samsung 32GB DDR5 RDIMM",
                    old_price_usd=125.0,
                    new_price_usd=110.0,
                    pct_change=-0.12,
                    new_listing=today10[0],
                ),
                PriceChange(
                    url="https://www.ebay.com/itm/1001",
                    title="Hynix 64GB DDR5 RDIMM",
                    old_price_usd=205.0,
                    new_price_usd=220.0,
                    pct_change=0.073,
                    new_listing=today10[1],
                ),
            ],
        ),
    )

    return out


def generate(force: bool = False) -> list[Path]:
    """Write all scenario payloads to ``fixtures/<name>.json``.

    Returns the list of paths written.
    """
    profile = load_profile(PROFILE_SLUG)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    snapshot = date(2026, 4, 28)
    for name, (listings, diff) in _scenarios().items():
        path = FIXTURES_DIR / f"{name}.json"
        if path.exists() and not force:
            written.append(path)
            continue
        payload: dict[str, Any] = build_input_payload(
            listings, diff=diff, profile=profile, snapshot_date=snapshot
        )
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    paths = generate(force=force)
    print(f"Wrote {len(paths)} fixture(s) to {FIXTURES_DIR}")
    for p in paths:
        print(f"  - {p.name}")
