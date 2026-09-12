"""Unit tests for adapter.search fan-out, merge, and partial failure."""

from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.adapter import (
    SearchBackendError,
    get_fanout_max_workers,
    merge_listings_by_id,
    search,
)
from rental_search_agent.models import Listing, RentalSearchFilters, RentalSearchResponse
from tests.fixtures.sample_data import sample_listing


def _resp(*listings: Listing) -> RentalSearchResponse:
    return RentalSearchResponse(listings=list(listings), total_count=len(listings))


class TestGetFanoutMaxWorkers:
    def test_default_is_five(self, monkeypatch):
        monkeypatch.delenv("APIFY_MAX_CONCURRENT", raising=False)
        assert get_fanout_max_workers() == 5

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("APIFY_MAX_CONCURRENT", "3")
        assert get_fanout_max_workers() == 3

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("APIFY_MAX_CONCURRENT", "nope")
        assert get_fanout_max_workers() == 5

    def test_clamps_to_at_least_one(self, monkeypatch):
        monkeypatch.setenv("APIFY_MAX_CONCURRENT", "0")
        assert get_fanout_max_workers() == 1


class TestMergeListingsById:
    def test_keeps_first_duplicate(self):
        a1 = sample_listing(id="mls-1", address="Vancouver")
        a2 = sample_listing(id="mls-1", address="Burnaby")
        b = sample_listing(id="mls-2", address="Surrey")
        merged = merge_listings_by_id([a1, a2, b])
        assert [lst.id for lst in merged] == ["mls-1", "mls-2"]
        assert merged[0].address == "Vancouver"

    def test_empty_ids_are_not_collapsed(self):
        a = sample_listing(id="", address="First")
        b = sample_listing(id="", address="Second")
        merged = merge_listings_by_id([a, b])
        assert len(merged) == 2
        assert [lst.address for lst in merged] == ["First", "Second"]


class TestAdapterSearchFanout:
    def test_single_city_sets_searched_locations(self):
        backend = MagicMock()
        listing = sample_listing(id="v1")
        backend.search.return_value = _resp(listing)
        filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver, BC")
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            result = search(filters)
        assert result.total_count == 1
        assert result.searched_locations == ["Vancouver, BC"]
        assert result.failed_locations == []
        backend.search.assert_called_once()
        assert backend.search.call_args.args[0].location == "Vancouver, BC"

    def test_multi_city_calls_backend_once_per_city_and_merges(self):
        backend = MagicMock()

        def _side_effect(filters):
            city = filters.location
            return _resp(sample_listing(id=f"id-{city}", address=city))

        backend.search.side_effect = _side_effect
        filters = RentalSearchFilters(
            min_bedrooms=2,
            location=["Vancouver, BC", "Burnaby, BC"],
        )
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            result = search(filters)
        assert backend.search.call_count == 2
        called = {call.args[0].location for call in backend.search.call_args_list}
        assert called == {"Vancouver, BC", "Burnaby, BC"}
        assert result.total_count == 2
        assert set(result.searched_locations) == {"Vancouver, BC", "Burnaby, BC"}
        assert result.failed_locations == []
        assert {lst.id for lst in result.listings} == {"id-Vancouver, BC", "id-Burnaby, BC"}

    def test_duplicate_listing_ids_are_deduped(self):
        backend = MagicMock()
        shared = sample_listing(id="shared-mls", address="Border")
        backend.search.side_effect = [_resp(shared), _resp(shared)]
        filters = RentalSearchFilters(
            min_bedrooms=1,
            location=["Vancouver, BC", "Burnaby, BC"],
        )
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            result = search(filters)
        assert result.total_count == 1
        assert result.listings[0].id == "shared-mls"

    def test_partial_failure_keeps_successes(self):
        backend = MagicMock()

        def _side_effect(filters):
            if filters.location == "Surrey, BC":
                raise SearchBackendError("The rental search is temporarily unavailable.")
            return _resp(sample_listing(id="ok", address=filters.location))

        backend.search.side_effect = _side_effect
        filters = RentalSearchFilters(
            min_bedrooms=1,
            location=["Vancouver, BC", "Surrey, BC"],
        )
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            result = search(filters)
        assert result.searched_locations == ["Vancouver, BC"]
        assert len(result.failed_locations) == 1
        assert result.failed_locations[0].location == "Surrey, BC"
        assert result.total_count == 1
        assert result.listings[0].id == "ok"

    def test_all_cities_fail_raises(self):
        backend = MagicMock()
        backend.search.side_effect = SearchBackendError("unavailable")
        filters = RentalSearchFilters(
            min_bedrooms=1,
            location=["Vancouver, BC", "Burnaby, BC"],
        )
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            with pytest.raises(SearchBackendError, match="all selected cities"):
                search(filters)

    def test_generic_exception_is_sanitized_on_partial_failure(self):
        backend = MagicMock()

        def _side_effect(filters):
            if filters.location == "Surrey, BC":
                raise RuntimeError("socket hang up secret internals")
            return _resp(sample_listing(id="ok", address=filters.location))

        backend.search.side_effect = _side_effect
        filters = RentalSearchFilters(
            min_bedrooms=1,
            location=["Vancouver, BC", "Surrey, BC"],
        )
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            result = search(filters)
        assert result.failed_locations[0].error == "The rental search is temporarily unavailable."
        assert "secret" not in result.failed_locations[0].error

    def test_north_vancouver_labels_fan_out_as_one_scrape(self):
        backend = MagicMock()
        backend.search.return_value = _resp(sample_listing(id="nv", address="North Vancouver"))
        filters = RentalSearchFilters(
            min_bedrooms=1,
            location=["City of North Vancouver", "District of North Vancouver"],
        )
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            result = search(filters)
        backend.search.assert_called_once()
        assert backend.search.call_args.args[0].location == "North Vancouver, BC"
        assert result.searched_locations == ["North Vancouver, BC"]

    def test_fanout_respects_apify_max_concurrent(self, monkeypatch):
        monkeypatch.setenv("APIFY_MAX_CONCURRENT", "2")
        backend = MagicMock()
        cities = ["Vancouver, BC", "Burnaby, BC", "Surrey, BC", "Richmond, BC"]
        filters = RentalSearchFilters(min_bedrooms=1, location=cities)

        future_by_city: dict[str, MagicMock] = {}

        def _submit(_fn, _backend, _filters, city):
            fut = MagicMock()
            fut.result.return_value = _resp(sample_listing(id=f"id-{city}", address=city))
            future_by_city[city] = fut
            return fut

        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            with patch("rental_search_agent.adapter.ThreadPoolExecutor") as pool_cls:
                pool = pool_cls.return_value.__enter__.return_value
                pool.submit.side_effect = _submit
                with patch(
                    "rental_search_agent.adapter.as_completed",
                    side_effect=lambda futures: list(futures),
                ):
                    result = search(filters)

        pool_cls.assert_called_once_with(max_workers=2)
        assert result.total_count == 4
        assert set(result.searched_locations) == set(cities)
