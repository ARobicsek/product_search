"""Shared test fixtures.

The single source of truth for *where the committed test-fixture profiles
live*. They deliberately do NOT live under the repo's ``products/`` tree: the
deployed web app rewrites/deletes ``products/<slug>/`` and commits straight to
origin/main on its own, so any profile the test suite + CI depend on must sit
somewhere the app never touches. See ADR-062.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from product_search.profile import (
    QVL,
    Profile,
    load_profile_from_path,
    load_qvl_from_path,
)

# worker/tests/fixtures/profiles  — pass as $PRODUCT_SEARCH_PRODUCTS_DIR to
# point the CLI loader (subprocess integration tests + the CI validate step)
# at these committed fixtures.
FIXTURE_PROFILES_DIR = Path(__file__).parent / "fixtures" / "profiles"

DDR5_SLUG = "ddr5-rdimm-256gb"
DDR5_FIXTURE_DIR = FIXTURE_PROFILES_DIR / DDR5_SLUG


def load_ddr5_profile(_slug: str = DDR5_SLUG) -> Profile:
    """Load the committed DDR5 fixture profile.

    The ``_slug`` arg exists so this can be a drop-in for
    ``product_search.profile.load_profile`` in tests that pass the slug
    positionally; it is intentionally ignored — the fixture path is fixed.
    """
    return load_profile_from_path(DDR5_FIXTURE_DIR / "profile.yaml")


def load_ddr5_qvl(_slug: str = DDR5_SLUG) -> QVL:
    """Load the committed DDR5 fixture QVL (see ``load_ddr5_profile``)."""
    return load_qvl_from_path(DDR5_FIXTURE_DIR / "qvl.yaml")


@pytest.fixture(scope="session")
def ddr5_profile() -> Profile:
    return load_ddr5_profile()


@pytest.fixture(scope="session")
def ddr5_qvl() -> QVL:
    return load_ddr5_qvl()
