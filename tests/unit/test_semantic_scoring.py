"""Unit tests for semantic_scoring.listing_to_text_blob and search_criteria_to_text_blob."""

from rental_search_agent.models import Listing
from rental_search_agent.semantic_scoring import (
    _format_count_range,
    _format_price_range,
    _proximity_rules_to_query_text,
    listing_to_text_blob,
    search_criteria_to_text_blob,
)


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


class TestListingToTextBlobDen:
    def test_has_den_appends_den_to_bedroom_phrase(self):
        listing = _listing(bedrooms=2, has_den=True)
        blob = listing_to_text_blob(listing)
        assert "2 bedrooms + den" in blob

    def test_no_den_omits_den_phrase(self):
        listing = _listing(bedrooms=2, has_den=False)
        blob = listing_to_text_blob(listing)
        assert "2 bedrooms" in blob
        assert "den" not in blob.lower()

    def test_has_den_none_omits_den_phrase(self):
        # has_den defaults to None for hand-built/older Listing dicts without the field set.
        listing = _listing(bedrooms=2)
        blob = listing_to_text_blob(listing)
        assert "2 bedrooms" in blob
        assert "den" not in blob.lower()


class TestFormatCountRange:
    def test_exact_value_mirrors_listing_single_value_phrasing(self):
        # min == max is the common case (e.g. "3 bed" -> min_bedrooms=max_bedrooms=3) and
        # must match listing_to_text_blob's own single-value phrasing ("3 bedrooms") exactly
        # for close lexical alignment between query and listing text.
        assert _format_count_range(3, 3, "bedrooms") == "3 bedrooms"

    def test_range_when_min_and_max_differ(self):
        assert _format_count_range(2, 4, "bedrooms") == "2-4 bedrooms"

    def test_min_only_is_at_least(self):
        assert _format_count_range(2, None, "bedrooms") == "at least 2 bedrooms"

    def test_max_only_is_up_to(self):
        assert _format_count_range(None, 4, "bedrooms") == "up to 4 bedrooms"

    def test_both_none_returns_empty(self):
        assert _format_count_range(None, None, "bedrooms") == ""

    def test_float_value_formatted_without_trailing_zero(self):
        assert _format_count_range(1.5, 1.5, "bathrooms") == "1.5 bathrooms"


class TestFormatPriceRange:
    def test_for_sale_single_value_uses_list_price(self):
        assert _format_price_range(999000, 999000, "for_sale") == "$999000 list price"

    def test_for_rent_single_value_uses_month(self):
        assert _format_price_range(2500, 2500, "for_rent") == "$2500/month"

    def test_for_sale_range(self):
        assert _format_price_range(800000, 1000000, "for_sale") == "$800000-$1000000 list price"

    def test_max_only_is_up_to(self):
        assert _format_price_range(None, 1000000, "for_sale") == "up to $1000000 list price"

    def test_min_only_is_at_least(self):
        assert _format_price_range(800000, None, "for_sale") == "at least $800000 list price"

    def test_unknown_listing_type_defaults_to_month_phrasing(self):
        assert _format_price_range(2500, 2500, None) == "$2500/month"

    def test_both_none_returns_empty(self):
        assert _format_price_range(None, None, "for_sale") == ""


class TestProximityRulesToQueryText:
    def test_single_rule_phrased_like_listing_proximity_text(self):
        rules = [{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}]
        assert _proximity_rules_to_query_text(rules) == "5 min walk to nearest transit station"

    def test_multiple_rules_joined(self):
        rules = [
            {"location": "downtown", "mode": "drive", "max_minutes": 20},
            {"location": "nearest transit station", "mode": "walk", "max_minutes": 5},
        ]
        text = _proximity_rules_to_query_text(rules)
        assert "20 min drive to downtown" in text
        assert "5 min walk to nearest transit station" in text

    def test_empty_or_none_returns_empty(self):
        assert _proximity_rules_to_query_text(None) == ""
        assert _proximity_rules_to_query_text([]) == ""

    def test_rule_missing_location_or_max_minutes_skipped(self):
        rules = [{"location": "", "mode": "walk", "max_minutes": 5}, {"location": "downtown", "mode": "walk"}]
        assert _proximity_rules_to_query_text(rules) == ""


class TestSearchCriteriaToTextBlob:
    def test_full_criteria_includes_all_parts_in_order(self):
        criteria = {
            "location": "Metrotown, Burnaby, BC",
            "min_bedrooms": 3,
            "max_bedrooms": 3,
            "price_max": 1000000,
            "listing_type": "for_sale",
        }
        blob = search_criteria_to_text_blob(
            criteria,
            qualitative_preferences="must have balcony, parking, storage",
            proximity_rules=[{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}],
        )
        assert blob == (
            "Metrotown, Burnaby, BC 3 bedrooms, up to $1000000 list price "
            "must have balcony, parking, storage 5 min walk to nearest transit station"
        )

    def test_omits_missing_parts(self):
        blob = search_criteria_to_text_blob({"location": "Vancouver, BC"})
        assert blob == "Vancouver, BC"

    def test_empty_criteria_and_preferences_returns_empty_string(self):
        assert search_criteria_to_text_blob({}) == ""

    def test_bathrooms_and_sqft_included_in_structured_line(self):
        criteria = {"min_bathrooms": 2, "max_bathrooms": 2, "min_sqft": 900, "max_sqft": 1200}
        blob = search_criteria_to_text_blob(criteria)
        assert blob == "2 bathrooms, 900-1200 sqft"

    def test_for_rent_price_phrasing(self):
        criteria = {"price_min": 2000, "price_max": 3000, "listing_type": "for_rent"}
        blob = search_criteria_to_text_blob(criteria)
        assert blob == "$2000-$3000/month"
