"""Integration tests for adapter.search with mocked Apify client."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.adapter import SearchBackendError, search
from rental_search_agent.backends.apify_realtor_ca import ApifyRealtorCaBackend
from rental_search_agent.models import RentalSearchFilters
from tests.fixtures.sample_data import mock_apify_item


def _make_items() -> list[dict]:
    return [
        mock_apify_item(mls="mls-1", address="100 Main St", bedrooms=2, bathrooms=2, size="1000 sqft", price=2500),
        mock_apify_item(mls="mls-2", address="200 Oak Ave", bedrooms=3, bathrooms=2, size="1500", price=3200),
        mock_apify_item(mls="mls-3", address="300 Pine Rd", bedrooms=1, bathrooms=1, size=600, price=1800, lat=None, lon=None),
    ]


def _mock_run(default_dataset_id: str = "dataset-1", status: str = "SUCCEEDED") -> SimpleNamespace:
    """Stand-in for apify_client's Run object: attribute access (default_dataset_id,
    status), not a dict — unlike dataset items, which the real SDK yields as dicts."""
    return SimpleNamespace(default_dataset_id=default_dataset_id, status=status)


def _mock_client(items: list[dict] | None = None, call_side_effect=None) -> MagicMock:
    client = MagicMock()
    actor = MagicMock()
    if call_side_effect is not None:
        actor.call.side_effect = call_side_effect
    else:
        actor.call.return_value = _mock_run()
    client.actor.return_value = actor
    dataset = MagicMock()
    dataset.iterate_items.return_value = iter(items if items is not None else _make_items())
    client.dataset.return_value = dataset
    return client


class TestAdapterSearch:
    def test_returns_listings_from_mock_items(self):
        client = _mock_client()
        backend = ApifyRealtorCaBackend(token="test-token", client=client, max_items=50)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver, BC")
            result = search(filters)

        assert result.total_count == 3
        assert len(result.listings) == 3
        assert result.listings[0].address == "100 Main St"
        assert result.listings[1].address == "200 Oak Ave"
        assert result.listings[2].address == "300 Pine Rd"
        run_input = client.actor.return_value.call.call_args.kwargs["run_input"]
        assert run_input["operation"] == "rent"
        assert run_input["location"] == "Vancouver, BC"
        assert run_input["minBeds"] == 1
        assert run_input["maxItems"] == 50

    def test_filter_by_min_bedrooms_post_fetch(self):
        client = _mock_client()
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=2, location="Vancouver")
            result = search(filters)

        assert result.total_count == 2
        assert all(l.bedrooms >= 2 for l in result.listings)

    def test_filter_by_rent_max(self):
        client = _mock_client()
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver", price_max=2500)
            result = search(filters)

        assert result.total_count == 2
        assert all(l.price <= 2500 for l in result.listings)

    def test_empty_after_filter_returns_empty_response(self):
        client = _mock_client()
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=10, location="Vancouver")
            result = search(filters)

        assert result.total_count == 0
        assert result.listings == []

    def test_empty_dataset_returns_empty_response(self):
        client = _mock_client(items=[])
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            result = search(filters)
        assert result.total_count == 0
        assert result.listings == []

    def test_sale_sets_buy_operation(self):
        client = _mock_client(items=[mock_apify_item(mls="s1", price=500000)])
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(
                min_bedrooms=2,
                location="Toronto, ON",
                listing_type="for_sale",
                price_max=900000,
            )
            result = search(filters)

        assert result.total_count == 1
        run_input = client.actor.return_value.call.call_args.kwargs["run_input"]
        assert run_input["operation"] == "buy"
        assert run_input["maxPrice"] == 900000
        assert "500,000" in (result.listings[0].price_display or "")

    def test_actor_exception_raises_backend_error(self):
        client = _mock_client(call_side_effect=Exception("Network error"))
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=2, location="Vancouver")
            with pytest.raises(SearchBackendError, match="temporarily unavailable"):
                search(filters)

    def test_missing_token_raises_backend_error(self):
        backend = ApifyRealtorCaBackend(token="", client=None)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            with pytest.raises(SearchBackendError, match="APIFY_TOKEN"):
                search(filters)

    def test_actor_still_running_raises_timeout_style_error(self):
        # Simulates wait_duration elapsing before the actor run finished.
        client = _mock_client()
        client.actor.return_value.call.return_value = _mock_run(status="RUNNING")
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            with pytest.raises(SearchBackendError, match="taking longer than expected"):
                search(filters)

    def test_actor_failed_status_raises_backend_error(self):
        client = _mock_client()
        client.actor.return_value.call.return_value = _mock_run(status="FAILED")
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            with pytest.raises(SearchBackendError, match="temporarily unavailable"):
                search(filters)

    def test_actor_call_passes_wait_duration(self):
        from datetime import timedelta

        client = _mock_client()
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            search(filters)
        wait_duration = client.actor.return_value.call.call_args.kwargs["wait_duration"]
        assert isinstance(wait_duration, timedelta)
        assert wait_duration.total_seconds() > 0

    def test_dict_shaped_run_result_is_supported(self):
        # apify-client < 3.0 (resolved on Python < 3.11 envs) returns a plain dict with
        # camelCase keys instead of a pydantic Run object with snake_case attributes.
        client = _mock_client()
        client.actor.return_value.call.return_value = {
            "defaultDatasetId": "dataset-1",
            "status": "SUCCEEDED",
        }
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            result = search(filters)
        assert result.total_count == 3

    def test_old_apify_client_wait_secs_fallback(self):
        # apify-client < 3.0's Actor.call() doesn't accept wait_duration; the backend
        # should retry with wait_secs (int) instead of crashing with a TypeError.
        client = _mock_client()

        def call_side_effect(*args, **kwargs):
            if "wait_duration" in kwargs:
                raise TypeError("call() got an unexpected keyword argument 'wait_duration'")
            assert "wait_secs" in kwargs
            assert isinstance(kwargs["wait_secs"], int)
            return _mock_run()

        client.actor.return_value.call.side_effect = call_side_effect
        backend = ApifyRealtorCaBackend(token="test-token", client=client)
        with patch("rental_search_agent.adapter.get_search_backend", return_value=backend):
            filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver")
            result = search(filters)
        assert result.total_count == 3
