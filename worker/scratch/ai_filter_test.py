import os
import json
from datetime import UTC, datetime
from product_search.profile import load_profile
from product_search.models import Listing
from product_search.validators.ai_filter import ai_filter

# clear fixture mode
os.environ.pop("WORKER_USE_FIXTURES", None)

profile = load_profile("ddr5-rdimm-256gb")
lst1 = Listing(
    source="ebay_search",
    url="http://ebay.com",
    title="Samsung DDR5 32GB 4800MHz RDIMM",
    fetched_at=datetime.now(tz=UTC),
    condition="new",
    is_kit=False,
    kit_module_count=1,
    unit_price_usd=100.0,
    attrs={"capacity_gb": 32, "speed_mts": 4800},
)
res = ai_filter([lst1], profile)
print(f"Passed: {len(res)}")
