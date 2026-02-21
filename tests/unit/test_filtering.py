"""Unit tests for rental_search_agent.filtering."""

import pytest

from rental_search_agent.filtering import (
    SORTABLE_ATTRS,
    _get_sort_key,
    _listing_matches,
    filter_listings,
)
from rental_search_agent.models import Listing, ListingFilterCriteria
from tests.fixtures.sample_data import sample_listing, sample_listings


class TestListingMatches:
    def test_none_criteria_matches_all(self):
        criteria = ListingFilterCriteria()
        listing = sample_listing(bedrooms=2, bathrooms=2, sqft=1000, price=2500)
        assert _listing_matches(listing, criteria) is True

    def test_min_bedrooms_match(self):
        criteria = ListingFilterCriteria(min_bedrooms=2)
        assert _listing_matches(sample_listing(bedrooms=2), criteria) is True
        assert _listing_matches(sample_listing(bedrooms=3), criteria) is True
        assert _listing_matches(sample_listing(bedrooms=1), criteria) is False

    def test_max_bedrooms_match(self):
        criteria = ListingFilterCriteria(max_bedrooms=2)
        assert _listing_matches(sample_listing(bedrooms=2), criteria) is True
        assert _listing_matches(sample_listing(bedrooms=1), criteria) is True
        assert _listing_matches(sample_listing(bedrooms=3), criteria) is False

    def test_min_bathrooms_match(self):
        criteria = ListingFilterCriteria(min_bathrooms=2)
        assert _listing_matches(sample_listing(bathrooms=2), criteria) is True
        assert _listing_matches(sample_listing(bathrooms=1.5), criteria) is False

    def test_rent_min_max_match(self):
        criteria = ListingFilterCriteria(rent_min=2000, rent_max=3000)
        assert _listing_matches(sample_listing(price=2500), criteria) is True
        assert _listing_matches(sample_listing(price=1500), criteria) is False
        assert _listing_matches(sample_listing(price=3500), criteria) is False

    def test_listing_with_none_sqft_fails_min_sqft(self):
        criteria = ListingFilterCriteria(min_sqft=500)
        assert _listing_matches(sample_listing(sqft=None), criteria) is False
        assert _listing_matches(sample_listing(sqft=600), criteria) is True

    def test_dict_input(self):
        criteria = ListingFilterCriteria(min_bedrooms=2)
        d = sample_listing(bedrooms=2).model_dump()
        assert _listing_matches(d, criteria) is True


class TestGetSortKey:
    def test_numeric_price(self):
        listing = sample_listing(price=2800)
        assert _get_sort_key(listing, "price") == (0, 2800.0)

    def test_none_sorts_to_end(self):
        listing = sample_listing(sqft=None)
        assert _get_sort_key(listing, "sqft") == (1, float("inf"))

    def test_string_attr(self):
        listing = sample_listing(address="123 Main St")
        assert _get_sort_key(listing, "address") == (0, "123 Main St")

    def test_dict_input(self):
        d = sample_listing(price=1000).model_dump()
        assert _get_sort_key(d, "price") == (0, 1000.0)


class TestFilterListings:
    def test_empty_list(self):
        from rental_search_agent.models import RentalSearchResponse

        result = filter_listings([], ListingFilterCriteria())
        assert isinstance(result, RentalSearchResponse)
        assert result.total_count == 0
        assert result.listings == []

    def test_all_match(self):
        listings = sample_listings(3)
        result = filter_listings(listings, ListingFilterCriteria())
        assert result.total_count == 3

    def test_partial_match(self):
        listings = [
            sample_listing(id="1", bedrooms=1),
            sample_listing(id="2", bedrooms=2),
            sample_listing(id="3", bedrooms=3),
        ]
        result = filter_listings(listings, ListingFilterCriteria(min_bedrooms=2))
        assert result.total_count == 2
        assert all(l.bedrooms >= 2 for l in result.listings)

    def test_sort_by_price_ascending(self):
        listings = [
            sample_listing(id="1", price=3000),
            sample_listing(id="2", price=2000),
            sample_listing(id="3", price=2500),
        ]
        result = filter_listings(
            listings, ListingFilterCriteria(), sort_by="price", ascending=True
        )
        assert [l.price for l in result.listings] == [2000.0, 2500.0, 3000.0]

    def test_sort_by_price_descending(self):
        listings = [
            sample_listing(id="1", price=2000),
            sample_listing(id="2", price=3000),
        ]
        result = filter_listings(
            listings, ListingFilterCriteria(), sort_by="price", ascending=False
        )
        assert [l.price for l in result.listings] == [3000.0, 2000.0]

    def test_dict_criteria(self):
        listings = sample_listings(2)
        result = filter_listings(listings, {"min_bedrooms": 1})
        assert result.total_count == 2

    def test_dict_listings(self):
        listings = [l.model_dump() for l in sample_listings(2)]
        result = filter_listings(listings, ListingFilterCriteria())
        assert result.total_count == 2
        assert all(isinstance(l, Listing) for l in result.listings)

    def test_sortable_attrs(self):
        assert "price" in SORTABLE_ATTRS
        assert "bedrooms" in SORTABLE_ATTRS
        assert "invalid_attr" not in SORTABLE_ATTRS

    def test_invalid_sort_by_ignored(self):
        listings = sample_listings(2)
        result = filter_listings(
            listings, ListingFilterCriteria(), sort_by="invalid", ascending=True
        )
        assert result.total_count == 2
