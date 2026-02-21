"""Integration tests for MCP server tools."""

from unittest.mock import patch

import pytest

from rental_search_agent.adapter import SearchBackendError
from rental_search_agent.models import Listing, RentalSearchResponse
from rental_search_agent.server import (
    ask_user,
    filter_listings,
    rental_search,
    simulate_viewing_request,
    summarize_listings,
)
from tests.fixtures.sample_data import sample_listing, sample_listings


class TestAskUser:
    def test_valid_payload(self):
        result = ask_user(prompt="Choose one", choices=["A", "B"], allow_multiple=False)
        assert result["request_user_input"] is True
        assert result["prompt"] == "Choose one"
        assert result["choices"] == ["A", "B"]
        assert result["allow_multiple"] is False

    def test_empty_prompt_raises(self):
        with pytest.raises(ValueError, match="prompt is required"):
            ask_user(prompt="", choices=["A"])

    def test_invalid_choices_type_raises(self):
        with pytest.raises(ValueError, match="choices must be a list"):
            ask_user(prompt="Q", choices="not a list")


class TestRentalSearch:
    def test_valid_filters_returns_response(self):
        sample = RentalSearchResponse(
            listings=[sample_listing()],
            total_count=1,
        )

        with patch("rental_search_agent.server.search", return_value=sample):
            result = rental_search(
                {"min_bedrooms": 2, "location": "Vancouver"}
            )
            assert result.total_count == 1
            assert len(result.listings) == 1
            assert result.listings[0].address == "123 Main St"

    def test_invalid_filters_raises(self):
        with pytest.raises(ValueError, match="Invalid filters"):
            rental_search({"location": "Vancouver"})

    def test_search_backend_error_becomes_value_error(self):
        with patch("rental_search_agent.server.search", side_effect=SearchBackendError("unavailable")):
            with pytest.raises(ValueError, match="unavailable"):
                rental_search({"min_bedrooms": 2, "location": "Vancouver"})


class TestFilterListings:
    def test_valid_listings_and_criteria(self):
        listings = [l.model_dump() for l in sample_listings(3)]
        result = filter_listings(
            listings,
            {"min_bedrooms": 2},
        )
        assert result.total_count == 3
        assert len(result.listings) == 3

    def test_filter_narrows_results(self):
        listings = [
            sample_listing(id="1", bedrooms=1).model_dump(),
            sample_listing(id="2", bedrooms=2).model_dump(),
            sample_listing(id="3", bedrooms=3).model_dump(),
        ]
        result = filter_listings(listings, {"min_bedrooms": 2})
        assert result.total_count == 2
        assert all(l.bedrooms >= 2 for l in result.listings)

    def test_sort_only(self):
        listings = [
            sample_listing(id="1", price=3000).model_dump(),
            sample_listing(id="2", price=2000).model_dump(),
        ]
        result = filter_listings(
            listings,
            {},
            sort_by="price",
            ascending=True,
        )
        assert result.listings[0].price == 2000
        assert result.listings[1].price == 3000

    def test_empty_listings_raises(self):
        with pytest.raises(ValueError, match="listings is required"):
            filter_listings([], {"min_bedrooms": 2})

    def test_no_criteria_and_no_sort_raises(self):
        listings = [sample_listing().model_dump()]
        with pytest.raises(ValueError, match="At least one filter criterion or sort_by"):
            filter_listings(listings, {})


class TestSummarizeListings:
    def test_valid_listings(self):
        listings = [l.model_dump() for l in sample_listings(2)]
        result = summarize_listings(listings)
        assert result["count"] == 2
        assert "price" in result
        assert "bedrooms" in result

    def test_empty_listings_raises(self):
        with pytest.raises(ValueError, match="listings is required"):
            summarize_listings([])


class TestSimulateViewingRequest:
    def test_valid_args(self):
        result = simulate_viewing_request(
            listing_url="https://example.com/listing/1",
            timeslot="Tuesday 6-8pm",
            user_details={"name": "Jane", "email": "jane@test.com"},
        )
        assert "Viewing request [simulated]" in result.summary
        assert "https://example.com/listing/1" in result.summary
        assert "Jane" in result.summary
        assert result.contact_url is not None
        assert "mailto:" in result.contact_url

    def test_empty_listing_url_raises(self):
        with pytest.raises(ValueError, match="listing_url"):
            simulate_viewing_request(
                listing_url="",
                timeslot="Tuesday",
                user_details={"name": "J", "email": "j@x.com"},
            )

    def test_empty_timeslot_raises(self):
        with pytest.raises(ValueError, match="timeslot"):
            simulate_viewing_request(
                listing_url="https://x.com",
                timeslot="",
                user_details={"name": "J", "email": "j@x.com"},
            )

    def test_invalid_user_details_raises(self):
        with pytest.raises(ValueError, match="Invalid user_details"):
            simulate_viewing_request(
                listing_url="https://x.com",
                timeslot="Tue",
                user_details={"email": "j@x.com"},
            )
