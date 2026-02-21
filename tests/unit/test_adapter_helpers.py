"""Unit tests for rental_search_agent.adapter helper functions."""

import pandas as pd
import pytest

from rental_search_agent.adapter import (
    SearchBackendError,
    _format_price_display,
    _parse_sqft,
    _row_to_listing,
)
from rental_search_agent.models import Listing
from tests.fixtures.sample_data import mock_pyRealtor_row


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

    def test_float_nan(self):
        assert _parse_sqft(float("nan")) is None

    def test_string_with_decimal(self):
        assert _parse_sqft("999.5 sqft") == 999.5


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


class TestRowToListing:
    def test_basic_mapping(self):
        row = mock_pyRealtor_row(
            mls="mls-99",
            address="456 Oak Ave",
            bedrooms=3,
            bathrooms=2.0,
            size="1500 sqft",
            rent=3200,
        )
        listing = _row_to_listing(row, "for_rent")
        assert isinstance(listing, Listing)
        assert listing.id == "mls-99"
        assert listing.address == "456 Oak Ave"
        assert listing.bedrooms == 3
        assert listing.bathrooms == 2.0
        assert listing.sqft == 1500.0
        assert listing.price == 3200
        assert listing.price_display is not None
        assert "$" in listing.price_display and "3" in listing.price_display

    def test_url_fallback_when_website_empty(self):
        row = mock_pyRealtor_row(mls="abc123", website="")
        listing = _row_to_listing(row, "for_rent")
        assert "realtor.ca" in listing.url
        assert "abc123" in listing.url

    def test_total_rent_preferred_for_rent(self):
        row = mock_pyRealtor_row(rent=2500, total_rent=2600)
        listing = _row_to_listing(row, "for_rent")
        assert listing.price == 2600

    def test_for_sale_uses_price(self):
        row_data = {
            "MLS": "m1",
            "Address": "1 St",
            "Bedrooms": 2,
            "Bathrooms": 2,
            "Size": 1000,
            "Price": 450000,
            "Website": "https://example.com",
            "Description": "House",
            "Postal Code": "",
            "Latitude": 0,
            "Longitude": 0,
            "House Category": "",
            "Ownership Category": "",
            "Ammenities": "",
            "Nearby Ammenities": "",
            "Open House": "",
            "Stories": 1,
        }
        row = pd.Series(row_data)
        listing = _row_to_listing(row, "for_sale")
        assert listing.price == 450000
        assert "450,000" in (listing.price_display or "")

    def test_missing_address_defaults(self):
        row = mock_pyRealtor_row(address="")
        listing = _row_to_listing(row, "for_rent")
        assert listing.address == "Address not provided"


class TestSearchBackendError:
    def test_import_and_raise(self):
        err = SearchBackendError("test message")
        assert str(err) == "test message"
        assert isinstance(err, Exception)

    def test_catch_and_reraise(self):
        try:
            raise SearchBackendError("unavailable")
        except SearchBackendError as e:
            assert "unavailable" in str(e)
