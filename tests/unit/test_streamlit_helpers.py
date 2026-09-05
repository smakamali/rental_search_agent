"""Unit tests for Streamlit app helper functions."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rental_search_agent.models import ProximityRule
from rental_search_agent.streamlit_app import (
    PREF_KEYS,
    _apply_proximity_filter_safeguard,
    _build_map_data,
    _listings_to_table_rows,
    _load_preferences_from_file,
    _preferences_block,
    _preferences_file,
    _save_preferences_to_file,
)


class TestPreferencesBlock:
    def test_empty_prefs(self):
        prefs = {k: "" for k in PREF_KEYS}
        result = _preferences_block(prefs)
        assert "No stored user preferences" in result
        assert "Ask for viewing preference" in result

    def test_with_viewing_name_email(self):
        prefs = {
            "viewing_preference": "weekends 10am",
            "name": "Jane",
            "email": "jane@test.com",
            "phone": "",
        }
        result = _preferences_block(prefs)
        assert "Stored user preferences" in result
        assert "viewing_preference = 'weekends 10am'" in result
        assert "name = 'Jane'" in result
        assert "email = 'jane@test.com'" in result
        assert "do not ask the user for these again" in result

    def test_with_phone(self):
        prefs = {
            "viewing_preference": "",
            "name": "Bob",
            "email": "bob@test.com",
            "phone": "555-1234",
        }
        result = _preferences_block(prefs)
        assert "phone = '555-1234'" in result

    def test_only_name_email_no_viewing(self):
        prefs = {
            "viewing_preference": "",
            "name": "Alice",
            "email": "alice@test.com",
            "phone": "",
        }
        result = _preferences_block(prefs)
        assert "name = 'Alice'" in result
        assert "email = 'alice@test.com'" in result

    def test_with_qualitative_preferences(self):
        prefs = {
            "viewing_preference": "",
            "name": "",
            "email": "",
            "phone": "",
            "proximity_preferences": "",
            "qualitative_preferences": "balcony, parking, gym",
        }
        result = _preferences_block(prefs)
        assert "Stored user preferences" in result
        assert "qualitative_preferences = 'balcony, parking, gym'" in result
        assert "score_listings_by_preferences" in result


class TestLoadPreferencesFromFile:
    def test_file_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.json"
            with patch("rental_search_agent.streamlit_app._preferences_file", return_value=path):
                result = _load_preferences_from_file()
                assert result == {k: "" for k in PREF_KEYS}

    def test_valid_json_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.json"
            path.write_text(json.dumps({"name": "Jane", "email": "j@x.com"}))
            with patch("rental_search_agent.streamlit_app._preferences_file", return_value=path):
                result = _load_preferences_from_file()
                assert result.get("name") == "Jane"
                assert result.get("email") == "j@x.com"

    def test_malformed_file_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.json"
            path.write_text("not valid json {{{")
            with patch("rental_search_agent.streamlit_app._preferences_file", return_value=path):
                result = _load_preferences_from_file()
                assert result == {k: "" for k in PREF_KEYS}


class TestSavePreferencesToFile:
    def test_save_then_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.json"
            with patch("rental_search_agent.streamlit_app._preferences_file", return_value=path):
                prefs = {
                    "viewing_preference": "weekdays",
                    "name": "Jane",
                    "email": "j@x.com",
                    "phone": "555",
                }
                _save_preferences_to_file(prefs)
                loaded = _load_preferences_from_file()
                assert loaded["name"] == "Jane"
                assert loaded["email"] == "j@x.com"
                assert loaded["viewing_preference"] == "weekdays"
                assert loaded["phone"] == "555"

    def test_directory_created_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "prefs.json"
            with patch("rental_search_agent.streamlit_app._preferences_file", return_value=path):
                _save_preferences_to_file({"name": "X", "email": "x@x.com"})
                assert path.exists()
                data = json.loads(path.read_text())
                assert data["name"] == "X"

    def test_no_op_on_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.json"
            with patch("rental_search_agent.streamlit_app._preferences_file", return_value=path):
                with patch.object(Path, "write_text", side_effect=OSError("Permission denied")):
                    _save_preferences_to_file({"name": "X", "email": "x@x.com"})
                assert not path.exists()

class TestDisplayRankUsage:
    """Regression tests for Sourcery finding: the Streamlit UI's local proximity
    closest-first safeguard reorders/filters listings independently of the LLM, which
    would desync the table/map numbering from the 'rank' the LLM uses for "listing N"
    references unless the UI renders using each listing's preserved 'rank' field.
    """

    def test_table_rows_use_rank_field_not_position(self):
        # Listings deliberately out of rank order (as they'd be after a local resort).
        listings = [
            {"id": "b", "rank": 2, "address": "B St"},
            {"id": "a", "rank": 1, "address": "A St"},
        ]
        rows = _listings_to_table_rows(listings)
        assert rows[0]["rank"] == 2
        assert rows[1]["rank"] == 1

    def test_table_rows_fall_back_to_position_when_rank_missing(self):
        listings = [{"id": "a", "address": "A St"}, {"id": "b", "address": "B St"}]
        rows = _listings_to_table_rows(listings)
        assert rows[0]["rank"] == 1
        assert rows[1]["rank"] == 2

    def test_map_labels_use_rank_field_not_position(self):
        listings = [
            {"id": "b", "rank": 2, "latitude": 49.28, "longitude": -123.12},
            {"id": "a", "rank": 1, "latitude": 49.29, "longitude": -123.13},
        ]
        points, _, _ = _build_map_data(listings)
        assert points[0]["label"] == "2"
        assert points[1]["label"] == "1"

    def test_proximity_safeguard_preserves_rank_across_filter_roundtrip(self):
        rule = ProximityRule(location="downtown", mode="drive", max_minutes=30)
        listings = [
            {
                "id": "a",
                "title": "A",
                "url": "https://x.com/a",
                "address": "1 A St",
                "price": 2000,
                "bedrooms": 1,
                "rank": 1,
                "proximity": {"downtown|drive": {"duration_min": 10, "distance_km": 5}},
            },
            {
                "id": "b",
                "title": "B",
                "url": "https://x.com/b",
                "address": "2 B St",
                "price": 2200,
                "bedrooms": 1,
                "rank": 2,
                "proximity": {"downtown|drive": {"duration_min": 20, "distance_km": 12}},
            },
        ]
        with patch(
            "rental_search_agent.streamlit_app.parse_proximity_preferences",
            return_value=[rule],
        ):
            result = _apply_proximity_filter_safeguard(listings, "30 min drive to downtown")
        result_by_id = {lst["id"]: lst for lst in result}
        assert result_by_id["a"]["rank"] == 1
        assert result_by_id["b"]["rank"] == 2


# Note: TestGetLatestSearchListings was removed — _get_latest_search_listings()
# (parsing listings out of message history after the fact) was superseded by the
# session-state-based display_list/master_list tracking introduced in the
# proximity-handling work, and no longer exists in streamlit_app.py.
