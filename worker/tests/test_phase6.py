import os
from pathlib import Path

import pytest

from product_search.models import AdapterQuery

# Tell the adapters to use fixtures
os.environ["WORKER_USE_FIXTURES"] = "1"

def test_nemixram_adapter():
    from product_search.adapters.nemixram import fetch
    query = AdapterQuery(source_id="nemixram_storefront", storefront_url="https://nemixram.com/collections/ddr5-rdimm")
    listings = fetch(query)
    
    assert len(listings) > 0
    # Our mock has 2 variants
    assert len(listings) == 2
    
    lst = listings[0]
    assert lst.source == "nemixram_storefront"
    assert "NEMIX RAM 64GB" in lst.title
    assert lst.unit_price_usd == 145.0
    assert lst.quantity_available == 50
    assert lst.mpn == "NEM-64G-4800"
    assert lst.attrs.get("capacity_gb") == 64
    assert lst.attrs.get("speed_mts") == 4800

def test_cloudstoragecorp_adapter():
    from product_search.adapters.cloudstoragecorp import fetch
    query = AdapterQuery(source_id="cloudstoragecorp_ebay", seller_id="cloudstoragecorp")
    listings = fetch(query)
    
    assert len(listings) > 0
    # Our mock has 2 listings
    assert len(listings) == 2
    
    lst = listings[0]
    assert lst.source == "cloudstoragecorp_ebay"
    assert "Samsung 64GB DDR5" in lst.title
    assert lst.unit_price_usd == 120.0
    assert lst.condition == "used"
    assert lst.url == "https://www.ebay.com/itm/11111111"
    assert lst.attrs.get("capacity_gb") == 64
    assert lst.attrs.get("speed_mts") == 4800
    
    lst2 = listings[1]
    assert lst2.condition == "new"
    assert lst2.unit_price_usd == 65.5

def test_memstore_adapter():
    from product_search.adapters.memstore import fetch
    query = AdapterQuery(source_id="memstore_ebay", seller_id="mem-store")
    listings = fetch(query)
    
    assert len(listings) > 0
    assert len(listings) == 2
    
    lst = listings[0]
    assert lst.source == "memstore_ebay"
    assert "Hynix 64GB DDR5" in lst.title
    assert lst.unit_price_usd == 135.0
    assert lst.condition == "used"
    
    lst2 = listings[1]
    # This listing has "Generic Brand" in title, so flag should be added.
    assert "generic_brand" in lst2.flags
