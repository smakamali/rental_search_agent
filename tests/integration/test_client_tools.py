"""Integration tests for client tool runner and helpers."""

import json
from unittest.mock import patch

import pytest

from rental_search_agent.client import _get_current_listings_from_messages, run_tool
from rental_search_agent.models import RentalSearchResponse
from tests.fixtures.sample_data import sample_listing, sample_listings


class TestRunTool:
    def test_ask_user_returns_request_payload(self):
        result = run_tool(
            "ask_user",
            {"prompt": "Choose?", "choices": ["A", "B"], "allow_multiple": False},
        )
        data = json.loads(result)
        assert data["request_user_input"] is True
        assert data["prompt"] == "Choose?"
        assert data["choices"] == ["A", "B"]

    def test_filter_listings_with_current_listings(self):
        listings = [l.model_dump() for l in sample_listings(3)]
        result = run_tool(
            "filter_listings",
            {"sort_by": "price", "ascending": True},
            current_listings=listings,
        )
        data = json.loads(result)
        assert "listings" in data
        assert len(data["listings"]) == 3

    def test_filter_listings_without_listings_returns_error(self):
        result = run_tool("filter_listings", {"sort_by": "price"})
        data = json.loads(result)
        assert "error" in data
        assert "Run a search first" in data["error"]

    def test_summarize_listings_with_listings(self):
        listings = [l.model_dump() for l in sample_listings(2)]
        result = run_tool("summarize_listings", {}, current_listings=listings)
        data = json.loads(result)
        assert data["count"] == 2
        assert "price" in data

    def test_summarize_listings_without_listings_returns_error(self):
        result = run_tool("summarize_listings", {})
        data = json.loads(result)
        assert "error" in data

    def test_simulate_viewing_request_valid(self):
        result = run_tool(
            "simulate_viewing_request",
            {
                "listing_url": "https://x.com/1",
                "timeslot": "Tue 6pm",
                "user_details": {"name": "Jane", "email": "j@x.com"},
            },
        )
        data = json.loads(result)
        assert "summary" in data
        assert "Viewing request [simulated]" in data["summary"]

    def test_simulate_viewing_request_invalid_returns_error(self):
        result = run_tool(
            "simulate_viewing_request",
            {
                "listing_url": "https://x.com",
                "timeslot": "Tue",
                "user_details": {"email": "j@x.com"},
            },
        )
        data = json.loads(result)
        assert "error" in data

    def test_rental_search_mocked(self):
        resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)
        with patch("rental_search_agent.client.search", return_value=resp):
            result = run_tool(
                "rental_search",
                {"filters": {"min_bedrooms": 2, "location": "Vancouver"}},
            )
            data = json.loads(result)
            assert data["total_count"] == 1
            assert len(data["listings"]) == 1

    def test_unknown_tool_returns_error(self):
        result = run_tool("unknown_tool", {})
        data = json.loads(result)
        assert "error" in data
        assert "Unknown tool" in data["error"]


class TestGetCurrentListingsFromMessages:
    def test_extracts_from_tool_result(self):
        listings = [{"id": "1", "address": "123 Main St"}]
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": json.dumps({"listings": listings})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == listings

    def test_most_recent_wins(self):
        old = [{"id": "old"}]
        new = [{"id": "new"}]
        messages = [
            {"role": "tool", "content": json.dumps({"listings": old})},
            {"role": "assistant", "content": "x"},
            {"role": "tool", "content": json.dumps({"listings": new})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == new

    def test_ignores_ask_user_results(self):
        messages = [
            {"role": "tool", "content": json.dumps({"answer": "Yes"})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == []

    def test_ignores_error_results(self):
        messages = [
            {"role": "tool", "content": json.dumps({"error": "search failed"})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == []

    def test_error_and_listings_listings_take_precedence(self):
        """When a tool result has both 'error' and 'listings', listings are returned (listings take precedence)."""
        listings = [{"id": "1", "address": "123 Main St"}]
        messages = [
            {
                "role": "tool",
                "content": json.dumps({"error": "partial failure", "listings": listings}),
            },
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == listings

    def test_malformed_json_skipped(self):
        listings = [{"id": "1"}]
        messages = [
            {"role": "tool", "content": "not json"},
            {"role": "tool", "content": json.dumps({"listings": listings})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == listings
