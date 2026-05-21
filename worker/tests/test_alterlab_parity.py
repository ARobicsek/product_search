"""T5 probe<->runtime AlterLab body parity (ADR-071).

The TS onboarder probe (`web/lib/onboard/alterlab-shared.ts::buildAlterlabBody`)
is a hand-maintained mirror of this runtime adapter's wire-body builder. The
missing `asp` (ADR-070) was exactly this kind of silent drift. This test pins
the Python half of the contract against a SHARED JSON fixture; the node half
(`web/scripts/check-alterlab-parity.test.mjs`) asserts the TS builder produces
the byte-identical body for the same fixture cases. If the two builders diverge,
one of the two test suites goes red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_search.adapters import universal_ai

_FIXTURE = Path(__file__).parent / "fixtures" / "alterlab_parity" / "body_cases.json"


def _load_cases() -> tuple[str, list[dict[str, Any]]]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data["url"], data["cases"]


def test_python_builder_matches_parity_fixture() -> None:
    url, cases = _load_cases()
    assert cases, "parity fixture has no cases"
    for case in cases:
        built = universal_ai._build_alterlab_body(url, case["options"])
        assert built == case["expected_body"], (
            f"Python _build_alterlab_body diverged from the parity contract on "
            f"case {case['name']!r}: built={built} expected={case['expected_body']}"
        )
