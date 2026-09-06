"""Unit tests for Streamlit app helper functions."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rental_search_agent.models import ProximityRule
from rental_search_agent.streamlit_app import (
    PREF_KEYS,
    _apply_default_match_score_sort,
    _apply_proximity_filter_safeguard,
    _build_map_data,
    _escape_markdown_link_text,
    _format_bedrooms,
    _format_days_on_market,
    _format_match_score,
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


class TestTableRowShape:
    """Regression tests for the Table and Analysis UI Tweaks change: MLS id column
    removed from the table, Days on Market and Match score columns added."""

    def test_no_mls_id_key(self):
        rows = _listings_to_table_rows([{"id": "a", "address": "A St", "rank": 1}])
        assert "MLS id" not in rows[0]

    def test_days_on_market_and_match_score_present_when_available(self):
        listings = [
            {
                "id": "a",
                "address": "A St",
                "rank": 1,
                "listing_age_hours": 48,
                "semantic_score": 0.873,
            }
        ]
        rows = _listings_to_table_rows(listings)
        assert rows[0]["days_on_market"] == "2d"
        assert rows[0]["match_score"] == "87%"

    def test_days_on_market_and_match_score_fall_back_to_dash(self):
        rows = _listings_to_table_rows([{"id": "a", "address": "A St", "rank": 1}])
        assert rows[0]["days_on_market"] == "—"
        assert rows[0]["match_score"] == "—"

    def test_bed_column_shows_den_notation_when_present(self):
        # Regression: the actor reports a den as e.g. "2 + 1" bedrooms (2 bedrooms + a
        # den); the table must surface that notation rather than just the plain bedrooms
        # count (which deliberately excludes the den — see _format_bedrooms).
        rows = _listings_to_table_rows(
            [{"id": "a", "address": "A St", "rank": 1, "bedrooms": 2, "bedrooms_display": "2 + 1"}]
        )
        assert rows[0]["bed"] == "2 + 1"

    def test_bed_column_falls_back_to_plain_bedrooms(self):
        rows = _listings_to_table_rows([{"id": "a", "address": "A St", "rank": 1, "bedrooms": 3}])
        assert rows[0]["bed"] == "3"


class TestFormatDaysOnMarket:
    def test_rounds_hours_to_days(self):
        assert _format_days_on_market({"listing_age_hours": 36}) == "2d"

    def test_missing_hours_returns_dash(self):
        assert _format_days_on_market({}) == "—"

    def test_non_numeric_hours_returns_dash(self):
        assert _format_days_on_market({"listing_age_hours": "n/a"}) == "—"


class TestFormatBedrooms:
    def test_prefers_bedrooms_display_when_present(self):
        assert _format_bedrooms({"bedrooms": 2, "bedrooms_display": "2 + 1"}) == "2 + 1"

    def test_falls_back_to_plain_bedrooms(self):
        assert _format_bedrooms({"bedrooms": 3}) == "3"

    def test_missing_bedrooms_returns_dash(self):
        assert _format_bedrooms({}) == "—"

    def test_empty_bedrooms_display_falls_back_to_plain_bedrooms(self):
        assert _format_bedrooms({"bedrooms": 3, "bedrooms_display": ""}) == "3"


class TestFormatMatchScore:
    def test_formats_score_as_percentage(self):
        assert _format_match_score({"semantic_score": 0.5}) == "50%"

    def test_missing_score_returns_dash(self):
        assert _format_match_score({}) == "—"

    def test_non_numeric_score_returns_dash(self):
        assert _format_match_score({"semantic_score": "n/a"}) == "—"


class TestEscapeMarkdownLinkText:
    """Security-review regression: the Analyze expander builds a markdown link whose
    label is a scraped MLS id (f"[{id}](url)"); a crafted id must not be able to close
    the label early and inject a second, attacker-controlled link."""

    def test_plain_id_unchanged(self):
        assert _escape_markdown_link_text("R3160716") == "R3160716"

    def test_escapes_brackets_that_would_break_out_of_label(self):
        malicious = "1234](https://attacker.example)[click me"
        escaped = _escape_markdown_link_text(malicious)
        assert "]" not in escaped.replace("\\]", "")
        assert "[" not in escaped.replace("\\[", "")
        assert escaped == "1234\\](https://attacker.example)\\[click me"

    def test_escapes_backslash(self):
        assert _escape_markdown_link_text("a\\b") == "a\\\\b"


class TestApplyDefaultMatchScoreSort:
    """Regression tests for the display-only default sort-by-match-score behavior."""

    def test_sorts_descending_by_semantic_score(self):
        listings = [
            {"id": "a", "rank": 1, "semantic_score": 0.2},
            {"id": "b", "rank": 2, "semantic_score": 0.9},
            {"id": "c", "rank": 3, "semantic_score": 0.5},
        ]
        result = _apply_default_match_score_sort(listings)
        assert [lst["id"] for lst in result] == ["b", "c", "a"]

    def test_listings_missing_score_sort_last(self):
        listings = [
            {"id": "a", "rank": 1, "semantic_score": 0.4},
            {"id": "b", "rank": 2},
            {"id": "c", "rank": 3, "semantic_score": 0.8},
        ]
        result = _apply_default_match_score_sort(listings)
        assert [lst["id"] for lst in result] == ["c", "a", "b"]

    def test_no_scores_leaves_order_unchanged(self):
        listings = [{"id": "a", "rank": 1}, {"id": "b", "rank": 2}]
        result = _apply_default_match_score_sort(listings)
        assert [lst["id"] for lst in result] == ["a", "b"]

    def test_does_not_mutate_rank_field(self):
        # Display-only sort: 'rank' (used for "listing N" LLM references) must survive
        # unchanged even though displayed order changes.
        listings = [
            {"id": "a", "rank": 1, "semantic_score": 0.1},
            {"id": "b", "rank": 2, "semantic_score": 0.9},
        ]
        result = _apply_default_match_score_sort(listings)
        result_by_id = {lst["id"]: lst for lst in result}
        assert result_by_id["a"]["rank"] == 1
        assert result_by_id["b"]["rank"] == 2
