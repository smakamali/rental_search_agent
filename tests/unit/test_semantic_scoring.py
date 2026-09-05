"""Unit tests for semantic_scoring.listing_to_text_blob."""

from rental_search_agent.models import Listing
from rental_search_agent.semantic_scoring import listing_to_text_blob


def _listing(**overrides) -> Listing:
    defaults = dict(
        id="m1",
        title="Nice place",
        url="https://example.com/1",
        address="123 Main St",
        price=2500,
        bedrooms=2,
        bathrooms=1.0,
    )
    defaults.update(overrides)
    return Listing(**defaults)


class TestListingToTextBlobPricePhrasing:
    def test_for_rent_uses_month_phrasing(self):
        listing = _listing(price=2500, listing_type="for_rent")
        blob = listing_to_text_blob(listing)
        assert "$2500/month" in blob

    def test_for_sale_uses_list_price_phrasing_not_month(self):
        # Regression: for-sale listings must not be described as monthly rent to the
        # semantic scorer, or preference matching/explanations misrepresent the price.
        listing = _listing(price=639000, listing_type="for_sale")
        blob = listing_to_text_blob(listing)
        assert "$639000 list price" in blob
        assert "/month" not in blob

    def test_unknown_listing_type_defaults_to_month_phrasing(self):
        # Back-compat: hand-built/older Listing dicts without listing_type set.
        listing = _listing(price=2500, listing_type=None)
        blob = listing_to_text_blob(listing)
        assert "$2500/month" in blob


class TestListingToTextBlobParking:
    def test_includes_parking_when_present(self):
        listing = _listing(parking_spaces=2, parking_type="Garage")
        blob = listing_to_text_blob(listing)
        assert "2 parking spaces (Garage)" in blob

    def test_singular_parking_space(self):
        listing = _listing(parking_spaces=1, parking_type=None)
        blob = listing_to_text_blob(listing)
        assert "1 parking space" in blob
        assert "1 parking spaces" not in blob

    def test_omits_parking_when_absent(self):
        listing = _listing(parking_spaces=None)
        blob = listing_to_text_blob(listing)
        assert "parking" not in blob.lower()
