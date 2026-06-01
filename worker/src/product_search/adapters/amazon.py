"""DataForSEO Amazon Products recall adapter (Phase 38, ADR-141).

Public API::

    from product_search.adapters.amazon import fetch

    listings = fetch(query, fixture_path=None)

Amazon US is absent from Serper's Google Shopping index (ADR-130/131), so it is
the single biggest recall gap and needs a dedicated source. This adapter reads
Amazon's catalog via the **DataForSEO Merchant "Amazon Products"** API, which
returns STRUCTURED JSON fields (``data_asin``/``title``/``url``/``price_from``/
``rating``/``image_url``), so the no-fabrication seam (ADR-001) holds — every
price and ASIN is a real field a deterministic fetch produced; the LLM never
reads an Amazon page. Fields the API doesn't carry (condition, stock count,
brand, ship-from) stay unknown, never guessed. This is NOT the retired Scrappey/
AlterLab browser-render path (ADR-139): no HTML parsing, no CAPTCHA treadmill.

Provider seam
-------------
The DataForSEO specifics are isolated behind ``_PROVIDER`` + the request/parse
helpers, so a recall-disappoint can swap to RapidAPI/Rainforest without touching
the pipeline (ADR-141 "written provider-agnostically").

Fixture mode
------------
Set ``WORKER_USE_FIXTURES=1`` (or pass ``fixture_path`` explicitly) to read a
saved DataForSEO response instead of hitting the network. Fixtures live in
``worker/tests/fixtures/amazon/*.json`` and are raw responses captured by the
Phase-38 spike (``tasks[0].result[0].items``).

Live mode
---------
HTTP Basic auth (``DATAFORSEO_LOGIN``/``DATAFORSEO_PASSWORD`` from env or a local
``.env``). The default **Standard queue** posts a task and polls for the result
(``task_post`` → ``task_get``); ``priority="live"`` uses the synchronous
``live/advanced`` endpoint (~2× cost). Missing creds raise ``AmazonAuthError``;
a non-200 envelope or a non-``20000`` **task-level** status raises
``AmazonAPIError`` — never a silent empty result. (Gotcha the spike surfaced: a
top-level ``status_code: 20000`` can mask a task-level error, so the task status
is checked, not just the envelope.)
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.models import AdapterQuery, Listing

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AmazonAuthError(RuntimeError):
    """Raised when DataForSEO credentials are absent."""


class AmazonAPIError(RuntimeError):
    """Raised when the DataForSEO API (envelope or task) returns an error."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_ID = "amazon_dataforseo"  # the ADAPTER id (NOT the merchant)
_PROVIDER = "dataforseo"          # thin provider seam; swap to swap providers

_API_BASE = "https://api.dataforseo.com/v3/merchant/amazon/products"
_LIVE_URL = f"{_API_BASE}/live/advanced"
_TASK_POST_URL = f"{_API_BASE}/task_post"
_TASK_GET_URL = f"{_API_BASE}/task_get/advanced/{{task_id}}"

# DataForSEO status codes.
_STATUS_OK = 20000
_STATUS_TASK_CREATED = 20100

# Only real product rows; the items array also carries editorial/related-search
# rows we never want as listings.
_PRODUCT_TYPES = {"amazon_serp", "amazon_paid"}

# Standard-queue polling (the cron worker tolerates the batch turnaround; ADR-141
# "the ~5–45-min batch turnaround is a non-issue for the cron worker"). Overridable
# via env for the live-verification session.
_HTTP_TIMEOUT = 60.0
_POLL_INTERVAL_S = float(os.environ.get("AMAZON_POLL_INTERVAL_S", "10"))
_MAX_POLL_S = float(os.environ.get("AMAZON_MAX_POLL_S", "360"))

_FIXTURE_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "amazon"

# Flat per-run API spend, mirroring ``ai_filter.LAST_RUN_USAGE``. Reset at the
# start of every ``fetch()`` and summed from the real DataForSEO ``cost`` field
# (no fabrication — recall is a flat-fee call, surfaced in the run-cost panel).
# ``None`` after a run that made no priced call (e.g. a fixture with no cost).
LAST_RUN_COST_USD: float | None = None


def _fixture_dir() -> Path:
    if _FIXTURE_DIR.is_dir():
        return _FIXTURE_DIR
    cwd_path = Path.cwd() / "tests" / "fixtures" / "amazon"
    if cwd_path.is_dir():
        return cwd_path
    for parent in Path.cwd().parents:
        candidate = parent / "worker" / "tests" / "fixtures" / "amazon"
        if candidate.is_dir():
            return candidate
    return _FIXTURE_DIR  # return original so the error message is informative


# ---------------------------------------------------------------------------
# Credential loading (env, then repo-root .env, then worker/.env)
# ---------------------------------------------------------------------------


def _env_files() -> list[Path]:
    """Candidate ``.env`` paths searched when creds aren't in the environment.

    A function (not a constant) so tests can monkeypatch it to ``[]`` and
    exercise the missing-credentials path deterministically.
    """
    here = Path(__file__).resolve()
    worker_dir = here.parent.parent.parent.parent
    return [worker_dir.parent / ".env", worker_dir / ".env"]


def _load_creds() -> tuple[str, str]:
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not (login and password):
        found: dict[str, str] = {}
        for env_path in _env_files():
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8").splitlines():
                for key in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"):
                    if line.startswith(key + "=") and key not in found:
                        found[key] = line.split("=", 1)[1].strip()
            if "DATAFORSEO_LOGIN" in found and "DATAFORSEO_PASSWORD" in found:
                break
        login = login or found.get("DATAFORSEO_LOGIN", "")
        password = password or found.get("DATAFORSEO_PASSWORD", "")
    if not (login and password):
        raise AmazonAuthError(
            "DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD must be set (env or .env) for "
            "live Amazon fetches. Set WORKER_USE_FIXTURES=1 to use saved fixtures instead."
        )
    return login, password


# ---------------------------------------------------------------------------
# Parsing helpers (shared by fixture + live)
# ---------------------------------------------------------------------------


def _check_task_status(task: dict[str, Any]) -> None:
    """Raise on a non-success **task-level** status (the spike gotcha).

    The top-level envelope can be ``20000`` while ``tasks[0]`` failed (e.g.
    ``40501 Invalid Field`` or ``40104 verify your account``). Treat any
    non-``20000`` task status as an API error.
    """
    code = task.get("status_code")
    if code != _STATUS_OK:
        msg = task.get("status_message") or "unknown error"
        raise AmazonAPIError(f"DataForSEO Amazon task failed ({code}): {msg}")


def _result_items(task: dict[str, Any]) -> list[dict[str, Any]]:
    result = task.get("result") or []
    if not result:
        return []
    items = result[0].get("items") or []
    return [it for it in items if it.get("type") in _PRODUCT_TYPES]


def _seller_name(item: dict[str, Any]) -> str:
    """The merchant shown to the user. Amazon search rows rarely name a seller;
    default to "Amazon" (the buy_url is an amazon.com link)."""
    seller = item.get("seller")
    if isinstance(seller, str) and seller.strip():
        return seller.strip()
    return "Amazon"


def _item_to_listing(item: dict[str, Any]) -> Listing:
    """Map one DataForSEO Amazon product row to a ``Listing``.

    ``attrs={"asin": ...}`` carries the ASIN (the stable Amazon id; future
    enrichment seam). ``condition`` is ``""`` (honest unknown), NOT ``"unknown"``:
    ``reject_condition_in`` passes on empty but would reject a literal
    ``"unknown"`` against ``condition_in:[new]`` — same pitfall the Serper
    adapter documents. ``url``/``buy_url`` are the same real amazon.com link
    DataForSEO returns (unlike Serper, Amazon yields a direct merchant URL).
    """
    rating = item.get("rating") or {}
    rating_value = rating.get("value")
    votes = rating.get("votes_count")

    price = item.get("price_from")
    url = item.get("url") or ""

    image_url = item.get("image_url")
    if not isinstance(image_url, str) or image_url.startswith("data:"):
        image_url = None

    asin = item.get("data_asin")
    attrs: dict[str, Any] = {}
    if asin:
        attrs["asin"] = asin

    return Listing(
        source=_SOURCE_ID,
        url=url,
        title=item.get("title") or "",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs=attrs,
        condition="",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=float(price) if isinstance(price, (int, float)) else 0.0,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=_seller_name(item),
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
        buy_url=url or None,
        image_url=image_url,
        rating=float(rating_value) if isinstance(rating_value, (int, float)) else None,
        rating_count=int(votes) if isinstance(votes, (int, float)) else None,
    )


def _tasks_to_listings(tasks: list[dict[str, Any]]) -> list[Listing]:
    """Status-check every task, then map + de-duplicate items by ASIN→url."""
    seen: set[str] = set()
    listings: list[Listing] = []
    for task in tasks:
        _check_task_status(task)
        for item in _result_items(task):
            key = str(item.get("data_asin") or item.get("url") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            listings.append(_item_to_listing(item))
    return listings


def _envelope_tasks(data: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    """Pull the tasks list + real spend from a DataForSEO response envelope."""
    tasks = data.get("tasks") or []
    cost = float(data.get("cost") or 0.0)
    return tasks, cost


# ---------------------------------------------------------------------------
# Fixture fetch
# ---------------------------------------------------------------------------


def _read_fixture_dir(query: AdapterQuery) -> tuple[list[dict[str, Any]], float]:
    fixture_dir = _fixture_dir()
    files = sorted(fixture_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"No fixture files found in {fixture_dir}. "
            "At least one .json fixture is required for WORKER_USE_FIXTURES=1 mode."
        )
    all_tasks: list[dict[str, Any]] = []
    cost = 0.0
    for fpath in files:
        data: dict[str, Any] = json.loads(fpath.read_text(encoding="utf-8"))
        tasks, c = _envelope_tasks(data)
        all_tasks.extend(tasks)
        cost += c
    return all_tasks, cost


# ---------------------------------------------------------------------------
# Live fetch (requires DataForSEO credentials)
# ---------------------------------------------------------------------------


def _task_dict(keyword: str, depth: int) -> dict[str, Any]:
    # language_name MUST be "English (United States)" (or language_code "en_US"),
    # NOT "English" — else the task fails 40501 (spike gotcha a).
    return {
        "keyword": keyword,
        "location_name": "United States",
        "language_name": "English (United States)",
        "se_domain": "amazon.com",
        "depth": depth,
    }


def _request_json(
    client: Any,
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    json_body: Any = None,
) -> dict[str, Any]:
    resp = client.request(method, url, headers=headers, json=json_body)
    if resp.status_code == 401:
        raise AmazonAuthError(
            "DataForSEO auth failed (401) — check DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD."
        )
    if resp.status_code != 200:
        raise AmazonAPIError(
            f"DataForSEO HTTP {resp.status_code} for {url}: {resp.text[:400]}"
        )
    data: dict[str, Any] = resp.json()
    return data


def _poll_task(client: Any, headers: dict[str, str], task_id: str) -> tuple[dict[str, Any], float]:
    """Poll ``task_get`` until the Standard-queue task completes (or times out).

    Field-validation errors (bad language_name, etc.) surface synchronously at
    ``task_post``; by the time we poll, the task is valid and merely pending, so
    a non-``20000`` status here means "not ready yet" — keep waiting until the
    bounded deadline, then raise.
    """
    url = _TASK_GET_URL.format(task_id=task_id)
    deadline = time.monotonic() + _MAX_POLL_S
    while True:
        data = _request_json(client, "GET", url, headers)
        cost = float(data.get("cost") or 0.0)
        tasks = data.get("tasks") or []
        if tasks and tasks[0].get("status_code") == _STATUS_OK:
            return tasks[0], cost
        if time.monotonic() >= deadline:
            raise AmazonAPIError(
                f"DataForSEO task {task_id} not ready after {_MAX_POLL_S:.0f}s."
            )
        time.sleep(_POLL_INTERVAL_S)


def _post_and_poll(
    client: Any, headers: dict[str, str], body: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float]:
    post = _request_json(client, "POST", _TASK_POST_URL, headers, json_body=body)
    cost = float(post.get("cost") or 0.0)
    ids: list[str] = []
    for t in post.get("tasks") or []:
        code = t.get("status_code")
        if code not in (_STATUS_OK, _STATUS_TASK_CREATED):
            raise AmazonAPIError(
                f"DataForSEO task_post rejected ({code}): {t.get('status_message')}"
            )
        tid = t.get("id")
        if tid:
            ids.append(str(tid))

    tasks: list[dict[str, Any]] = []
    for tid in ids:
        task, c = _poll_task(client, headers, tid)
        tasks.append(task)
        cost += c
    return tasks, cost


def _fetch_live(query: AdapterQuery) -> tuple[list[Listing], float]:
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "httpx is required for live Amazon fetches. Run: pip install 'httpx>=0.27'"
        ) from exc

    login, password = _load_creds()
    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

    priority = str(query.extra.get("priority", "standard"))
    depth = int(query.extra.get("depth", 48))
    body = [_task_dict(q, depth) for q in query.queries]

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        if priority == "live":
            data = _request_json(client, "POST", _LIVE_URL, headers, json_body=body)
            tasks, cost = _envelope_tasks(data)
        else:
            tasks, cost = _post_and_poll(client, headers, body)

    return _tasks_to_listings(tasks), cost


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch(
    query: AdapterQuery,
    *,
    fixture_path: Path | None = None,
) -> list[Listing]:
    """Fetch Amazon listings for the given query via DataForSEO.

    Args:
        query: Search parameters; ``queries`` drives one Amazon task each,
            ``extra`` may carry ``depth`` (result rows, default 48) and
            ``priority`` (``"standard"`` task-queue, default, or ``"live"``).
        fixture_path: If given, load this specific DataForSEO JSON file instead
            of hitting the network (single-file fixture mode used in tests).

    Returns:
        A de-duplicated list of ``Listing`` objects (may be empty). The real API
        spend is recorded in the module-level ``LAST_RUN_COST_USD``.

    Raises:
        ``AmazonAuthError``: live mode with no/invalid credentials.
        ``AmazonAPIError``: a non-200 envelope or a non-20000 task status.
        ``FileNotFoundError``: fixture-dir mode with no fixtures present.
    """
    global LAST_RUN_COST_USD
    LAST_RUN_COST_USD = None

    use_fixtures = (
        os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
        or fixture_path is not None
    )

    if use_fixtures:
        if fixture_path is not None:
            data: dict[str, Any] = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
            tasks, cost = _envelope_tasks(data)
        else:
            tasks, cost = _read_fixture_dir(query)
        listings = _tasks_to_listings(tasks)
        LAST_RUN_COST_USD = cost
        return listings

    listings, cost = _fetch_live(query)
    LAST_RUN_COST_USD = cost
    return listings
