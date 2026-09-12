"""Unit tests for Apify backend Phase 1 logging (mocked client, no network)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rental_search_agent.backends.apify_realtor_ca import ApifyRealtorCaBackend
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.models import RentalSearchFilters
from tests.fixtures.sample_data import mock_apify_item


def _mock_run(default_dataset_id: str = "dataset-1", status: str = "SUCCEEDED") -> SimpleNamespace:
    return SimpleNamespace(default_dataset_id=default_dataset_id, status=status)


def _mock_client(items: list | None = None, call_side_effect=None) -> MagicMock:
    client = MagicMock()
    actor = MagicMock()
    if call_side_effect is not None:
        actor.call.side_effect = call_side_effect
    else:
        actor.call.return_value = _mock_run()
    client.actor.return_value = actor
    dataset = MagicMock()
    dataset.iterate_items.return_value = iter(items if items is not None else [])
    client.dataset.return_value = dataset
    return client


class TestApifySearchLogging:
    def test_logs_start_and_success(self, caplog):
        items = [mock_apify_item(mls="mls-1", bedrooms=2, price=2500)]
        client = _mock_client(items=items)
        backend = ApifyRealtorCaBackend(
            token="test-token", client=client, max_items=50, max_retries=0
        )
        filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver, BC")
        # Start/success are DEBUG so adapter.search can own the INFO search boundary.
        with caplog.at_level("DEBUG", logger="rental_search_agent.backends.apify_realtor_ca"):
            result = backend.search(filters)
        assert result.total_count == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("Apify search start" in m and "location=Vancouver, BC" in m for m in messages)
        assert any("maxItems=50" in m and "fetchDetails=" in m for m in messages)
        assert any(
            "Apify search success" in m
            and "raw_items=1" in m
            and "listings=1" in m
            and "attempt=1" in m
            for m in messages
        )
        # No stacked elapsed_ms on the success line (log_stage owns timing).
        assert not any("Apify search success" in m and "elapsed_ms=" in m for m in messages)
        info_msgs = [
            r.getMessage()
            for r in caplog.records
            if r.levelname == "INFO"
            and ("Apify search start" in r.getMessage() or "Apify search success" in r.getMessage()
                 or "Apify fetch start" in r.getMessage())
        ]
        assert info_msgs == []
        joined = " ".join(messages)
        assert "test-token" not in joined

    def test_warns_on_empty_success(self, caplog):
        client = _mock_client(items=[])
        backend = ApifyRealtorCaBackend(
            token="test-token", client=client, max_retries=0
        )
        filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver, BC")
        with caplog.at_level("WARNING", logger="rental_search_agent.backends.apify_realtor_ca"):
            result = backend.search(filters)
        assert result.total_count == 0
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("0 items" in m and "Vancouver, BC" in m for m in warnings)

    def test_retry_warning_includes_location(self, caplog):
        client = _mock_client(
            call_side_effect=[Exception("boom"), _mock_run()],
        )
        client.dataset.return_value.iterate_items.return_value = iter(
            [mock_apify_item(mls="mls-1", bedrooms=2, price=2500)]
        )
        backend = ApifyRealtorCaBackend(
            token="test-token",
            client=client,
            max_retries=1,
            retry_base_seconds=0.0,
        )
        filters = RentalSearchFilters(min_bedrooms=1, location="Burnaby, BC")
        with caplog.at_level("WARNING", logger="rental_search_agent.backends.apify_realtor_ca"):
            backend.search(filters)
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "Apify attempt" in m and "location=Burnaby, BC" in m and "operation=rent" in m
            for m in warnings
        )

    def test_exhausted_retries_still_raise(self):
        client = _mock_client(call_side_effect=Exception("always fail"))
        backend = ApifyRealtorCaBackend(
            token="test-token", client=client, max_retries=0
        )
        filters = RentalSearchFilters(min_bedrooms=1, location="Vancouver, BC")
        with pytest.raises(SearchBackendError, match="temporarily unavailable"):
            backend.search(filters)
