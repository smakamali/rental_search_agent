"""Unit tests for rental_search_agent.proximity_parser."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.proximity_parser import parse_proximity_preferences
from rental_search_agent.models import ProximityRule


def _make_llm_response(rules_payload: list) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response with the given rules."""
    content = json.dumps({"rules": rules_payload})
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    return mock_resp


def _valid_rule(location="Downtown Vancouver", mode="drive", max_minutes=30):
    return {"location": location, "mode": mode, "max_minutes": max_minutes}


class TestParseProximityPreferencesEmpty:
    def test_empty_string_returns_empty_list(self):
        result = parse_proximity_preferences("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = parse_proximity_preferences("   ")
        assert result == []

    def test_none_raises_or_returns_empty(self):
        # The function guards `if not proximity_text` — None is falsy
        result = parse_proximity_preferences(None)
        assert result == []


class TestParseProximityPreferencesValid:
    def test_single_rule_parsed_correctly(self):
        rules_payload = [_valid_rule()]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(rules_payload)

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = parse_proximity_preferences("30 min drive to downtown Vancouver")

        assert len(result) == 1
        assert isinstance(result[0], ProximityRule)
        assert result[0].location == "Downtown Vancouver"
        assert result[0].mode == "drive"
        assert result[0].max_minutes == 30

    def test_multiple_rules_parsed(self):
        rules_payload = [
            _valid_rule("Downtown Vancouver", "drive", 30),
            _valid_rule("nearest transit station", "walk", 5),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(rules_payload)

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = parse_proximity_preferences("30 min drive to downtown; 5 min walk to skytrain")

        assert len(result) == 2
        assert result[1].location == "nearest transit station"
        assert result[1].mode == "walk"

    def test_duplicate_rules_deduplicated(self):
        rules_payload = [
            _valid_rule("Downtown Vancouver", "drive", 30),
            _valid_rule("Downtown Vancouver", "drive", 30),
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(rules_payload)

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = parse_proximity_preferences("30 min drive to downtown Vancouver twice")

        assert len(result) == 1

    def test_invalid_rule_items_skipped(self):
        rules_payload = [
            {"location": "Downtown Vancouver", "mode": "drive", "max_minutes": 30},
            {"location": "Bad Rule"},  # missing required fields
            {"location": "UBC", "mode": "transit", "max_minutes": 45},
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_llm_response(rules_payload)

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = parse_proximity_preferences("drive to downtown; transit to UBC")

        assert len(result) == 2
        assert result[0].location == "Downtown Vancouver"
        assert result[1].location == "UBC"

    def test_unexpected_json_shape_returns_empty(self):
        """LLM returns a dict without 'rules' key."""
        content = json.dumps({"something_else": "value"})
        mock_message = MagicMock()
        mock_message.content = content
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = parse_proximity_preferences("some preference text")

        assert result == []


class TestParseProximityPreferencesErrors:
    def test_llm_call_failure_raises_value_error(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            with pytest.raises(ValueError, match="Failed to parse proximity preferences"):
                parse_proximity_preferences("30 min drive to downtown")

    def test_llm_returns_invalid_json_raises_value_error(self):
        mock_message = MagicMock()
        mock_message.content = "not valid json {{{"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            with pytest.raises(ValueError, match="LLM returned invalid JSON"):
                parse_proximity_preferences("30 min drive")

    def test_missing_api_key_raises_value_error(self):
        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            side_effect=ValueError("No LLM API key found"),
        ):
            with pytest.raises(ValueError, match="No LLM API key found"):
                parse_proximity_preferences("30 min drive to downtown")

    def test_llm_returns_none_content_returns_empty(self):
        """LLM returns empty content — parsed as empty dict → no rules key → []."""
        mock_message = MagicMock()
        mock_message.content = "{}"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch(
            "rental_search_agent.proximity_parser._make_llm_client",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = parse_proximity_preferences("some text")

        assert result == []
