"""Unit tests for rental_search_agent.summarizer."""

import pytest

from rental_search_agent.summarizer import summarize_listings
from tests.fixtures.sample_data import sample_listing, sample_listings


class TestSummarizeListings:
    def test_empty_list(self):
        result = summarize_listings([])
        assert result["count"] == 0
        assert result["price"] is None
        assert result["bedrooms"]["distribution"] == {}
        assert result["bathrooms"]["count_with_data"] == 0
        assert result["sqft"] is None
        assert result["house_category"] == {}

    def test_with_listings_price_stats(self):
        listings = [
            sample_listing(price=1000),
            sample_listing(price=2000),
            sample_listing(price=3000),
        ]
        result = summarize_listings(listings)
        assert result["count"] == 3
        assert result["price"]["min"] == 1000
        assert result["price"]["max"] == 3000
        assert result["price"]["median"] == 2000
        assert result["price"]["mean"] == 2000

    def test_bedroom_distribution(self):
        listings = [
            sample_listing(id="1", bedrooms=1),
            sample_listing(id="2", bedrooms=2),
            sample_listing(id="3", bedrooms=2),
        ]
        result = summarize_listings(listings)
        dist = result["bedrooms"]["distribution"]
        assert dist["1"] == 1
        assert dist["2"] == 2

    def test_bathroom_stats(self):
        listings = [
            sample_listing(bathrooms=1),
            sample_listing(bathrooms=2),
            sample_listing(bathrooms=2),
        ]
        result = summarize_listings(listings)
        assert result["bathrooms"]["count_with_data"] == 3
        assert result["bathrooms"]["min"] == 1.0
        assert result["bathrooms"]["max"] == 2.0

    def test_sqft_stats(self):
        listings = [
            sample_listing(sqft=800),
            sample_listing(sqft=1200),
        ]
        result = summarize_listings(listings)
        assert result["sqft"]["count_with_data"] == 2
        assert result["sqft"]["min"] == 800
        assert result["sqft"]["max"] == 1200

    def test_house_category(self):
        listings = [
            sample_listing(house_category="Apartment"),
            sample_listing(house_category="Apartment"),
            sample_listing(house_category="House"),
        ]
        result = summarize_listings(listings)
        assert result["house_category"]["Apartment"] == 2
        assert result["house_category"]["House"] == 1

    def test_listings_with_missing_fields(self):
        listings = [
            sample_listing(price=1000, sqft=None, bathrooms=None),
        ]
        result = summarize_listings(listings)
        assert result["count"] == 1
        assert result["price"] is not None
        assert result["sqft"] is None
        assert result["bathrooms"]["count_with_data"] == 0

    def test_dict_input(self):
        listings = [l.model_dump() for l in sample_listings(2)]
        result = summarize_listings(listings)
        assert result["count"] == 2
