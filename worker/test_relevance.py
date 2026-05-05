import asyncio
from product_search.validators.ai_filter import filter_listings
from product_search.models import Listing, Profile, Target, Rule

async def test():
    profile = Profile(
        slug="breville-barista-express",
        display_name="Breville Barista Express Espresso Machine",
        description="A home espresso machine with built-in grinder. Usually stainless steel or black.",
        target=Target(amount=1, unit="machine", configurations=[]),
        spec_filters=[Rule(rule="in_stock"), Rule(rule="title_excludes", values=["used", "refurbished"])]
    )
    
    lst = Listing(
        source="universal_ai_search",
        url="https://www.amazon.com/dp/B0D8VPJ9XZ",
        title="2PCS 54mm Silicone Steam Ring for Breville Espresso Machine 878/870/860/880/810/840/450/500 and Sage 880/878/875/870/810/500, Breville Espresso Machine Accessories Grouphead Gasket Replacement Part",
        brand="Unknown",
        mpn="Unknown",
        attrs={},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=9.98,
        quantity_available=10,
        seller_name="Amazon",
        seller_rating_pct=100.0,
        seller_feedback_count=100
    )
    
    passed, rejected = await filter_listings([lst], profile)
    print("Passed:", len(passed))
    print("Rejected:", len(rejected))
    if rejected:
        print("Reason:", rejected[0][1])

if __name__ == "__main__":
    asyncio.run(test())
