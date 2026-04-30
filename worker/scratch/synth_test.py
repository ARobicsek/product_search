import os
import json
from datetime import UTC, datetime
from dotenv import load_dotenv
load_dotenv('../.env')

from product_search.profile import load_profile
from product_search.models import Listing
from product_search.synthesizer import synthesize
from product_search.storage.diff import DiffResult, PriceChange

os.environ.pop('WORKER_USE_FIXTURES', None)
profile = load_profile('ddr5-rdimm-256gb')
lst1 = Listing(
    source='ebay_search', url='http://ebay.com/item1', title='Samsung DDR5 32GB 4800MHz RDIMM', 
    fetched_at=datetime.now(tz=UTC), condition='new', is_kit=False, kit_module_count=1, 
    unit_price_usd=100.0, attrs={'capacity_gb': 32, 'speed_mts': 4800}, brand='Samsung', 
    mpn='M32', kit_price_usd=None, quantity_available=10, seller_name='test', 
    seller_rating_pct=100.0, seller_feedback_count=100, ship_from_country='US', total_for_target_usd=800.0
)

diff = DiffResult(
    new=[lst1],
    dropped=[],
    changed=[
        PriceChange(
            url="http://ebay.com/item2",
            title="Old item",
            old_price_usd=120.0,
            new_price_usd=100.0,
            pct_change=-0.166,
            new_listing=lst1,
        )
    ]
)

res = synthesize([lst1], diff, profile, provider="anthropic", model="claude-haiku-4-5")
print(res.report_md)
