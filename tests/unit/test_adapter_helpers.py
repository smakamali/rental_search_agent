"""Unit tests for Apify Realtor.ca backend helpers and mapping."""

import pytest

from rental_search_agent.backends.apify_realtor_ca import (
    _format_price_display,
    _parse_bedrooms,
    _parse_sqft,
    filters_to_run_input,
    item_to_listing,
    post_filter_listings,
)
from rental_search_agent.backends.common import _coerce_float
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.models import Listing, RentalSearchFilters
from tests.fixtures.sample_data import mock_apify_item


class TestParseSqft:
    def test_string_with_sqft(self):
        assert _parse_sqft("1200 sqft") == 1200.0
        assert _parse_sqft("1500 sq ft") == 1500.0

    def test_numeric(self):
        assert _parse_sqft(1200) == 1200.0
        assert _parse_sqft(1000.5) == 1000.5

    def test_none(self):
        assert _parse_sqft(None) is None

    def test_empty_string(self):
        assert _parse_sqft("") is None
        assert _parse_sqft("   ") is None

    def test_string_with_decimal(self):
        assert _parse_sqft("999.5 sqft") == 999.5

    def test_metric_string_converted_to_sqft(self):
        # Real Toronto-area payloads report SizeInterior in square metres (e.g. condos
        # around 700 sqft come back as "65.0316 m2").
        assert _parse_sqft("65.0316 m2") == pytest.approx(699.99, abs=0.5)

    def test_metric_string_with_superscript(self):
        assert _parse_sqft("50 m\u00b2") == pytest.approx(538.2, abs=0.5)

    def test_numeric_value_not_treated_as_metric(self):
        # Plain numbers (no unit string) are assumed to already be sqft.
        assert _parse_sqft(650) == 650.0


class TestCoerceFloat:
    def test_negative_string_preserves_sign(self):
        # The Apify actor returns Longitude as a negative-number *string* (e.g.
        # '-123.116411' for Vancouver). Stripping non-digit chars naively would drop
        # the '-' and silently flip listings to the wrong hemisphere (e.g. China).
        assert _coerce_float("-123.116411") == pytest.approx(-123.116411)

    def test_positive_string(self):
        assert _coerce_float("49.278794") == pytest.approx(49.278794)

    def test_currency_string_unaffected(self):
        assert _coerce_float("$2,500") == 2500.0

    def test_native_negative_number_unaffected(self):
        assert _coerce_float(-123.5) == -123.5

    def test_none(self):
        assert _coerce_float(None) is None


class TestFormatPriceDisplay:
    def test_raw_formatted_preserved(self):
        assert _format_price_display("$2,500/month", 2500, "for_rent") == "$2,500/month"
        assert _format_price_display("$1,000", 1000, "for_sale") == "$1,000"

    def test_rent_fallback(self):
        assert _format_price_display(None, 2800, "for_rent") == "$2,800/month"
        assert _format_price_display("", 1500, "for_rent") == "$1,500/month"

    def test_sale_fallback(self):
        assert _format_price_display(None, 500000, "for_sale") == "$500,000"

    def test_zero_price(self):
        assert _format_price_display(None, 0, "for_rent") is None


class TestItemToListing:
    def test_basic_rent_mapping(self):
        item = mock_apify_item(
            mls="mls-99",
            address="456 Oak Ave",
            bedrooms=3,
            bathrooms=2.0,
            size="1500 sqft",
            price=3200,
        )
        listing = item_to_listing(item, "for_rent")
        assert isinstance(listing, Listing)
        assert listing.id == "mls-99"
        assert listing.address == "456 Oak Ave"
        assert listing.bedrooms == 3
        assert listing.bathrooms == 2.0
        assert listing.sqft == 1500.0
        assert listing.price == 3200
        assert listing.price_display is not None
        assert "$" in listing.price_display
        assert listing.source == "Realtor.ca"
        assert listing.latitude == 49.28

    def test_url_fallback_when_relative_missing(self):
        item = mock_apify_item(mls="abc123", relative_url="")
        listing = item_to_listing(item, "for_rent")
        assert "realtor.ca" in listing.url
        assert "abc123" in listing.url

    def test_for_sale_price_display(self):
        item = mock_apify_item(mls="m1", price=450000, price_display="$450,000")
        listing = item_to_listing(item, "for_sale")
        assert listing.price == 450000
        assert "450,000" in (listing.price_display or "")

    def test_missing_address_defaults(self):
        item = mock_apify_item(address="")
        listing = item_to_listing(item, "for_rent")
        assert listing.address == "Address not provided"

    def test_rent_uses_lease_rent_fields(self):
        # Real igolaizola payloads for rent operations use Property.LeaseRent /
        # LeaseRentUnformattedValue, not Price/PriceUnformattedValue (which are only
        # present for buy/for_sale operations). Verified against a live actor run.
        item = mock_apify_item(mls="m1")
        item["Property"].pop("Price", None)
        item["Property"].pop("PriceUnformattedValue", None)
        item["Property"]["LeaseRent"] = "$2,990/Monthly"
        item["Property"]["LeaseRentUnformattedValue"] = "2990"
        listing = item_to_listing(item, "for_rent")
        assert listing.price == 2990.0
        assert "2,990" in (listing.price_display or "")

    def test_sale_prefers_price_over_lease_rent(self):
        item = mock_apify_item(mls="m1", price=639000)
        item["Property"]["LeaseRent"] = "$2,990/Monthly"
        item["Property"]["LeaseRentUnformattedValue"] = "2990"
        listing = item_to_listing(item, "for_sale")
        assert listing.price == 639000.0

    def test_postal_code_at_item_root_is_used(self):
        # Real payloads put PostalCode at the item root, not under Property.Address.
        item = mock_apify_item(mls="m1")
        del item["Property"]["Address"]["PostalCode"]
        item["PostalCode"] = "V6Z1Z7"
        listing = item_to_listing(item, "for_rent")
        assert listing.postal_code == "V6Z1Z7"

    def test_pipe_delimited_address_normalized(self):
        item = mock_apify_item(mls="m1")
        item["Property"]["Address"]["AddressText"] = "709 1009 HARWOOD STREET|Vancouver, British Columbia V6Z1Z7"
        listing = item_to_listing(item, "for_rent")
        assert "|" not in listing.address
        assert listing.address == "709 1009 HARWOOD STREET, Vancouver, British Columbia V6Z1Z7"

    def test_parking_fields_extracted_when_present(self):
        # Property.ParkingSpaceTotal/ParkingType are explicit structured fields on the
        # actor payload (verified against a live run for houses/townhouses).
        item = mock_apify_item(mls="m1", parking_spaces="4", parking_type="Garage, Carport")
        listing = item_to_listing(item, "for_sale")
        assert listing.parking_spaces == 4
        assert listing.parking_type == "Garage, Carport"

    def test_parking_fields_none_when_absent(self):
        # Not every listing has dedicated parking (e.g. some condos); must stay None
        # rather than defaulting to 0, so callers can distinguish "unknown" from "none".
        item = mock_apify_item(mls="m1")
        listing = item_to_listing(item, "for_rent")
        assert listing.parking_spaces is None
        assert listing.parking_type is None

    def test_negative_longitude_string_not_flipped(self):
        # Real igolaizola payloads report Latitude/Longitude as numeric *strings*
        # (e.g. '-123.116411' for Vancouver), not floats. A naive digit-stripping
        # coercion drops the '-' and silently relocates listings to the wrong
        # hemisphere (observed: Vancouver plotted in China on the results map).
        item = mock_apify_item(mls="m1", lat="49.278794", lon="-123.116411")
        listing = item_to_listing(item, "for_rent")
        assert listing.latitude == pytest.approx(49.278794)
        assert listing.longitude == pytest.approx(-123.116411)

    def test_trusts_realtor_ca_absolute_url(self):
        item = mock_apify_item(mls="m1", relative_url="https://www.realtor.ca/real-estate/1/m1")
        listing = item_to_listing(item, "for_rent")
        assert listing.url == "https://www.realtor.ca/real-estate/1/m1"

    def test_rejects_offsite_absolute_url(self):
        item = mock_apify_item(mls="m1", relative_url="https://attacker.example/phish")
        listing = item_to_listing(item, "for_rent")
        assert "attacker.example" not in listing.url
        assert listing.url == "https://www.realtor.ca/listing/m1"

    def test_accepts_relative_path_url(self):
        item = mock_apify_item(mls="m1", relative_url="/real-estate/1/m1")
        listing = item_to_listing(item, "for_rent")
        assert listing.url == "https://www.realtor.ca/real-estate/1/m1"

    def test_photo_url_rejected_off_allowlisted_cdn(self):
        # Security-review/Bugbot regression: photo_url is scraped third-party data
        # rendered as a raw <img src> in the UI, so anything off the trusted CDN host
        # must be dropped rather than passed through.
        item = mock_apify_item(mls="m1")
        item["Property"]["Photo"] = [{"MedResPath": "https://attacker.example/evil.jpg"}]
        listing = item_to_listing(item, "for_rent")
        assert listing.photo_url is None

    def test_photo_url_accepted_on_allowlisted_cdn(self):
        item = mock_apify_item(mls="m1")
        item["Property"]["Photo"] = [{"MedResPath": "https://cdn.realtor.ca/listings/m1/photo.jpg"}]
        listing = item_to_listing(item, "for_rent")
        assert listing.photo_url == "https://cdn.realtor.ca/listings/m1/photo.jpg"

    def test_video_url_rejected_when_non_https(self):
        # Security-review regression: video_url is rendered as a clickable markdown
        # link in the Analyze expander; dangerous schemes (javascript:, data:, plain
        # http) must not pass through.
        item = mock_apify_item(mls="m1")
        item["AlternateURL"] = {"VideoLink": "javascript:alert(1)"}
        listing = item_to_listing(item, "for_rent")
        assert listing.video_url is None

    def test_video_url_rejected_when_malformed(self):
        item = mock_apify_item(mls="m1")
        item["AlternateURL"] = {"VideoLink": "not a url"}
        listing = item_to_listing(item, "for_rent")
        assert listing.video_url is None

    def test_video_url_accepted_when_well_formed_https(self):
        item = mock_apify_item(mls="m1")
        item["AlternateURL"] = {"VideoLink": "https://www.youtube.com/watch?v=abc123"}
        listing = item_to_listing(item, "for_rent")
        assert listing.video_url == "https://www.youtube.com/watch?v=abc123"


class TestFiltersToRunInput:
    def test_rent_operation_and_bounds(self):
        filters = RentalSearchFilters(
            min_bedrooms=2,
            max_bedrooms=3,
            location="Vancouver, BC",
            listing_type="for_rent",
            price_min=2000,
            price_max=3500,
            min_bathrooms=1,
            min_sqft=800,
        )
        run_input = filters_to_run_input(filters, max_items=40)
        assert run_input["operation"] == "rent"
        assert run_input["location"] == "Vancouver, BC"
        assert run_input["maxItems"] == 40
        assert run_input["minBeds"] == 2
        assert run_input["maxBeds"] == 3
        assert run_input["minPrice"] == 2000
        assert run_input["maxPrice"] == 3500
        assert run_input["minBathrooms"] == 1
        assert run_input["minSquareFootage"] == 800

    def test_sale_operation(self):
        filters = RentalSearchFilters(
            min_bedrooms=2,
            location="Toronto, ON",
            listing_type="for_sale",
            price_max=900000,
        )
        run_input = filters_to_run_input(filters, max_items=100)
        assert run_input["operation"] == "buy"
        assert run_input["maxPrice"] == 900000

    def test_rejects_for_sale_or_rent(self):
        filters = RentalSearchFilters.model_construct(
            min_bedrooms=1,
            location="Vancouver",
            listing_type="for_sale_or_rent",
        )
        with pytest.raises(SearchBackendError, match="for_rent"):
            filters_to_run_input(filters, max_items=10)


class TestPostFilter:
    def test_filters_by_rent_max(self):
        listings = [
            item_to_listing(mock_apify_item(mls="a", price=2000), "for_rent"),
            item_to_listing(mock_apify_item(mls="b", price=4000), "for_rent"),
        ]
        filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver", price_max=2500)
        out = post_filter_listings(listings, filters)
        assert len(out) == 1
        assert out[0].id == "a"


class TestSearchBackendError:
    def test_import_and_raise(self):
        err = SearchBackendError("test message")
        assert str(err) == "test message"
        assert isinstance(err, Exception)


class TestParseBedrooms:
    """The actor reports a den by joining it onto the bedroom count as e.g. '1 + 1' rather
    than a separate field. Regression coverage for the bug where naive int-coercion turned
    '1 + 1' into 11 (stripping the '+' and space, concatenating the digits)."""

    def test_den_notation_splits_into_primary_and_den_count(self):
        assert _parse_bedrooms("1 + 1") == (1, 1)
        assert _parse_bedrooms("2 + 1") == (2, 1)

    def test_den_notation_without_spaces(self):
        assert _parse_bedrooms("3+1") == (3, 1)

    def test_plain_number_has_no_den(self):
        assert _parse_bedrooms("3") == (3, 0)
        assert _parse_bedrooms(3) == (3, 0)

    def test_none_or_empty_defaults_to_zero_no_den(self):
        assert _parse_bedrooms(None) == (0, 0)
        assert _parse_bedrooms("") == (0, 0)

    def test_does_not_mangle_den_notation_into_a_large_number(self):
        # The regression this guards against: _coerce_int("1 + 1") == 11.
        bedrooms, den_count = _parse_bedrooms("1 + 1")
        assert bedrooms == 1
        assert bedrooms != 11


class TestItemToListingDen:
    def test_den_notation_maps_to_primary_bedrooms_has_den_and_display(self):
        item = mock_apify_item(bedrooms="2 + 1")
        listing = item_to_listing(item, "for_sale")
        assert listing.bedrooms == 2
        assert listing.has_den is True
        assert listing.bedrooms_display == "2 + 1"

    def test_plain_bedrooms_has_no_den_and_no_display(self):
        item = mock_apify_item(bedrooms=3)
        listing = item_to_listing(item, "for_rent")
        assert listing.bedrooms == 3
        assert listing.has_den is False
        assert listing.bedrooms_display is None
