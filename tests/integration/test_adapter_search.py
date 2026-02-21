"""Integration tests for adapter.search with mocked pyRealtor."""

import sys

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from rental_search_agent.adapter import SearchBackendError, search
from rental_search_agent.models import RentalSearchFilters


def _make_test_df() -> pd.DataFrame:
    """Create a minimal DataFrame mimicking pyRealtor houses_df output."""
    rows = [
        {
            "MLS": "mls-1",
            "Address": "100 Main St",
            "Bedrooms": 2,
            "Bathrooms": 2,
            "Size": "1000 sqft",
            "Rent": 2500,
            "Website": "https://realtor.ca/1",
            "Description": "Nice place",
            "Postal Code": "V6B 1A1",
            "Latitude": 49.28,
            "Longitude": -123.12,
            "House Category": "Apartment",
            "Ownership Category": "Condominium",
            "Ammenities": "",
            "Nearby Ammenities": "",
            "Open House": "",
            "Stories": 1,
        },
        {
            "MLS": "mls-2",
            "Address": "200 Oak Ave",
            "Bedrooms": 3,
            "Bathrooms": 2,
            "Size": "1500",
            "Rent": 3200,
            "Website": "https://realtor.ca/2",
            "Description": "Spacious",
            "Postal Code": "V6B 2B2",
            "Latitude": 49.29,
            "Longitude": -123.11,
            "House Category": "House",
            "Ownership Category": "",
            "Ammenities": "",
            "Nearby Ammenities": "",
            "Open House": "",
            "Stories": 2,
        },
        {
            "MLS": "mls-3",
            "Address": "300 Pine Rd",
            "Bedrooms": 1,
            "Bathrooms": 1,
            "Size": 600,
            "Rent": 1800,
            "Website": "",
            "Description": "Cozy",
            "Postal Code": "",
            "Latitude": None,
            "Longitude": None,
            "House Category": "Apartment",
            "Ownership Category": "",
            "Ammenities": "",
            "Nearby Ammenities": "",
            "Open House": "",
            "Stories": 1,
        },
    ]
    return pd.DataFrame(rows)


class TestAdapterSearch:
    def test_returns_listings_from_mock_df(self):
        df = _make_test_df()
        mock_facade = MagicMock()
        mock_facade.houses_df = df
        mock_pyRealtor = MagicMock()
        mock_pyRealtor.HousesFacade.return_value = mock_facade

        with patch.dict(sys.modules, {"pyRealtor": mock_pyRealtor}):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            result = search(filters)

        assert result.total_count == 3
        assert len(result.listings) == 3
        assert result.listings[0].address == "100 Main St"
        assert result.listings[1].address == "200 Oak Ave"
        assert result.listings[2].address == "300 Pine Rd"

    def test_filter_by_min_bedrooms(self):
        df = _make_test_df()
        mock_facade = MagicMock()
        mock_facade.houses_df = df
        mock_pyRealtor = MagicMock()
        mock_pyRealtor.HousesFacade.return_value = mock_facade

        with patch.dict(sys.modules, {"pyRealtor": mock_pyRealtor}):
            filters = RentalSearchFilters(
                min_bedrooms=2,
                location="Vancouver",
            )
            result = search(filters)

        assert result.total_count == 2
        assert all(l.bedrooms >= 2 for l in result.listings)

    def test_filter_by_rent_max(self):
        df = _make_test_df()
        mock_facade = MagicMock()
        mock_facade.houses_df = df
        mock_pyRealtor = MagicMock()
        mock_pyRealtor.HousesFacade.return_value = mock_facade

        with patch.dict(sys.modules, {"pyRealtor": mock_pyRealtor}):
            filters = RentalSearchFilters(
                min_bedrooms=1,
                location="Vancouver",
                rent_max=2500,
            )
            result = search(filters)

        assert result.total_count == 2
        assert all(l.price <= 2500 for l in result.listings)

    def test_empty_after_filter_returns_empty_response(self):
        """When all rows are filtered out, returns empty list."""
        df = _make_test_df()
        mock_facade = MagicMock()
        mock_facade.houses_df = df
        mock_pyRealtor = MagicMock()
        mock_pyRealtor.HousesFacade.return_value = mock_facade

        with patch.dict(sys.modules, {"pyRealtor": mock_pyRealtor}):
            filters = RentalSearchFilters(
                min_bedrooms=10,
                location="Vancouver",
            )
            result = search(filters)

        assert result.total_count == 0
        assert result.listings == []

    def test_search_save_houses_exception_raises_backend_error(self):
        mock_facade = MagicMock()
        mock_facade.search_save_houses.side_effect = Exception("Network error")
        mock_pyRealtor = MagicMock()
        mock_pyRealtor.HousesFacade.return_value = mock_facade

        with patch.dict(sys.modules, {"pyRealtor": mock_pyRealtor}):
            filters = RentalSearchFilters(min_bedrooms=2, location="Vancouver")
            with pytest.raises(SearchBackendError, match="temporarily unavailable"):
                search(filters)

    def test_search_raises_error_when_pyrealtor_missing(self):
        """When pyRealtor is missing or broken, search raises SearchBackendError."""
        with patch.dict(sys.modules, {"pyRealtor": None}, clear=False):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            with pytest.raises(SearchBackendError):
                search(filters)
