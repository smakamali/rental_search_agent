"""Unit tests for Phase 3 entrypoint / boundary logging."""

import json
import logging
from unittest.mock import patch

import pytest

from rental_search_agent.adapter import SearchBackendError
from rental_search_agent.client import (
    _load_preferences_from_file,
    run_tool,
)
from rental_search_agent.logging_config import clear_run_id, get_run_id
from rental_search_agent.models import RentalSearchResponse
from rental_search_agent.server import rental_search as mcp_rental_search
from tests.fixtures.sample_data import sample_listing


@pytest.fixture(autouse=True)
def _clear_run_id():
    clear_run_id()
    yield
    clear_run_id()


class TestRunToolRentalSearchLogging:
    def test_backend_failure_logs_warning(self, caplog):
        filters = {"min_bedrooms": 2, "location": "Vancouver, BC"}
        with (
            caplog.at_level(logging.WARNING, logger="rental_search_agent.client"),
            patch(
                "rental_search_agent.client.search",
                side_effect=SearchBackendError("Apify timeout"),
            ),
            patch("rental_search_agent.client._persist_fill_in_from_chat"),
        ):
            result = run_tool("rental_search", {"filters": filters})

        data = json.loads(result)
        assert "error" in data
        assert "Apify timeout" in data["error"]
        messages = [r.getMessage() for r in caplog.records]
        assert any("rental_search backend failure" in m for m in messages)
        assert get_run_id() is None

    def test_success_logs_start_and_end_info(self, caplog):
        filters = {"min_bedrooms": 1, "location": "Toronto"}
        resp = RentalSearchResponse(
            listings=[sample_listing()],
            total_count=1,
            searched_locations=["Toronto"],
            failed_locations=[],
        )
        with (
            caplog.at_level(logging.INFO, logger="rental_search_agent.client"),
            patch("rental_search_agent.client.search", return_value=resp),
            patch("rental_search_agent.client._persist_fill_in_from_chat"),
        ):
            result = run_tool("rental_search", {"filters": filters})

        data = json.loads(result)
        assert data["total_count"] == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("rental_search start" in m and "location=" in m for m in messages)
        assert any("rental_search end" in m and "total_count=1" in m for m in messages)
        assert not any("sk-" in m or "api_key" in m.lower() for m in messages)

    def test_calendar_api_failure_logs_warning(self, caplog):
        with (
            caplog.at_level(logging.WARNING, logger="rental_search_agent.client"),
            patch(
                "rental_search_agent.client.calendar_get_available_slots",
                side_effect=ValueError("credentials missing"),
            ),
        ):
            result = run_tool(
                "calendar_get_available_slots",
                {
                    "preferred_times": "weekday evenings",
                    "date_range_start": "2026-03-01T00:00:00",
                    "date_range_end": "2026-03-14T23:59:59",
                },
            )
        data = json.loads(result)
        assert "error" in data
        assert any(
            "calendar_get_available_slots failed" in r.getMessage()
            for r in caplog.records
        )


class TestClientPreferencesLoadLogging:
    def test_corrupt_preferences_file_warns(self, caplog, tmp_path, monkeypatch):
        from rental_search_agent.session_runtime import clear_runtime

        clear_runtime()
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text("{not-json", encoding="utf-8")
        monkeypatch.setattr(
            "rental_search_agent.client._preferences_file",
            lambda: prefs_path,
        )
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.preference_store"):
            loaded = _load_preferences_from_file()
        assert isinstance(loaded, dict)
        assert any("Failed to load preferences" in r.getMessage() for r in caplog.records)


class TestStreamlitPreferencesLoadLogging:
    def test_corrupt_preferences_file_warns(self, caplog, tmp_path, monkeypatch):
        from rental_search_agent.streamlit_app import (
            _load_preferences_from_file as st_load,
        )

        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(
            "rental_search_agent.streamlit_app._preferences_file",
            lambda: prefs_path,
        )
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.preference_store"):
            loaded = st_load()
        assert isinstance(loaded, dict)
        assert any("Failed to load preferences" in r.getMessage() for r in caplog.records)


class TestMcpRentalSearchLogging:
    def test_backend_failure_logs_warning_before_raise(self, caplog):
        filters = {"min_bedrooms": 2, "location": "Vancouver"}
        with (
            caplog.at_level(logging.WARNING, logger="rental_search_agent.server"),
            patch(
                "rental_search_agent.server.search",
                side_effect=SearchBackendError("backend down"),
            ),
            pytest.raises(ValueError, match="backend down"),
        ):
            mcp_rental_search(filters)
        assert any(
            "rental_search backend failure" in r.getMessage() for r in caplog.records
        )

    def test_success_logs_complete_counts(self, caplog):
        filters = {"min_bedrooms": 1, "location": "Ottawa"}
        resp = RentalSearchResponse(
            listings=[sample_listing()],
            total_count=1,
            searched_locations=["Ottawa"],
        )
        with (
            caplog.at_level(logging.INFO, logger="rental_search_agent.server"),
            patch("rental_search_agent.server.search", return_value=resp),
        ):
            out = mcp_rental_search(filters)
        assert out.total_count == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("rental_search start" in m for m in messages)
        assert any("rental_search complete" in m and "total_count=1" in m for m in messages)


class TestServerCalendarErrorLogging:
    def test_calendar_error_logs_and_preserves_cause(self, caplog):
        from rental_search_agent.server import calendar_list_events

        with (
            caplog.at_level(logging.WARNING, logger="rental_search_agent.server"),
            patch(
                "rental_search_agent.server.do_calendar_list_events",
                side_effect=RuntimeError("oauth expired"),
            ),
            pytest.raises(ValueError) as exc_info,
        ):
            calendar_list_events("2026-01-01T00:00:00", "2026-01-02T00:00:00")
        assert exc_info.value.__cause__ is not None
        assert "oauth expired" in str(exc_info.value.__cause__)
        assert any("Calendar tool failed" in r.getMessage() for r in caplog.records)
