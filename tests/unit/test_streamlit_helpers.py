"""Unit tests for Streamlit app helper functions."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rental_search_agent.streamlit_app import (
    PREF_KEYS,
    _get_latest_search_listings,
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


class TestGetLatestSearchListings:
    def test_empty_messages(self):
        assert _get_latest_search_listings([]) == []

    def test_no_tool_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        assert _get_latest_search_listings(messages) == []

    def test_tool_message_with_listings(self):
        listings = [{"id": "1", "address": "123 Main St", "price": 2500}]
        messages = [
            {"role": "tool", "content": json.dumps({"listings": listings})},
        ]
        result = _get_latest_search_listings(messages)
        assert result == listings

    def test_most_recent_wins(self):
        old_listings = [{"id": "old"}]
        new_listings = [{"id": "new"}]
        messages = [
            {"role": "tool", "content": json.dumps({"listings": old_listings})},
            {"role": "assistant", "content": "x"},
            {"role": "tool", "content": json.dumps({"listings": new_listings})},
        ]
        result = _get_latest_search_listings(messages)
        assert result == new_listings

    def test_malformed_json_skipped(self):
        messages = [
            {"role": "tool", "content": "not json"},
            {"role": "tool", "content": json.dumps({"listings": [{"id": "1"}]})},
        ]
        result = _get_latest_search_listings(messages)
        assert result == [{"id": "1"}]

    def test_message_without_listings_skipped(self):
        messages = [
            {"role": "tool", "content": json.dumps({"answer": "yes"})},
        ]
        assert _get_latest_search_listings(messages) == []
