"""Unit tests for client.run_agent_step LLM loop."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.client import run_agent_step
from rental_search_agent.models import RentalSearchResponse
from tests.fixtures.sample_data import sample_available_slots, sample_listing, sample_listings


# ---------------------------------------------------------------------------
# Helpers to build mock OpenAI responses
# ---------------------------------------------------------------------------

def _make_final_reply(content: str = "Here are the results.") -> MagicMock:
    """LLM returns a plain assistant message with no tool calls."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_tool_call_reply(name: str, arguments: dict, call_id: str = "call-1") -> MagicMock:
    """LLM returns a response requesting one tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)

    msg = MagicMock()
    msg.content = ""
    msg.tool_calls = [tc]
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_client(*responses) -> tuple[MagicMock, str]:
    """Build a mock OpenAI client that returns the given responses in sequence."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = list(responses)
    return mock_client, "gpt-4o-mini"


def _base_messages():
    return [{"role": "system", "content": "You are a rental search assistant."}]


# ---------------------------------------------------------------------------
# Tests: final reply (no tool calls)
# ---------------------------------------------------------------------------

class TestRunAgentStepFinalReply:
    def test_final_reply_returns_messages_and_none_payload(self):
        client, model = _make_client(_make_final_reply("Hello!"))
        messages = _base_messages() + [{"role": "user", "content": "Hi"}]

        updated, payload, _ = run_agent_step(client, model, messages)

        assert payload is None
        assert updated[-1]["role"] == "assistant"
        assert updated[-1]["content"] == "Hello!"

    def test_final_reply_appends_to_existing_messages(self):
        client, model = _make_client(_make_final_reply("Done."))
        messages = _base_messages() + [{"role": "user", "content": "Search for 2 beds"}]

        updated, _, _ = run_agent_step(client, model, messages)

        assert len(updated) == len(messages) + 1
        assert updated[-1]["content"] == "Done."


# ---------------------------------------------------------------------------
# Tests: tool call → execute → continue to final reply
# ---------------------------------------------------------------------------

class TestRunAgentStepToolCall:
    def test_single_tool_call_executed_and_result_in_messages(self):
        """LLM calls rental_search → result appended → LLM returns final reply."""
        sample_resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)
        tool_call = _make_tool_call_reply(
            "rental_search",
            {"filters": {"min_bedrooms": 2, "location": "Vancouver"}},
        )
        final = _make_final_reply("Found 1 listing.")

        with patch("rental_search_agent.client.search", return_value=sample_resp):
            client, model = _make_client(tool_call, final)
            messages = _base_messages() + [{"role": "user", "content": "Find 2 bed"}]
            updated, payload, _ = run_agent_step(client, model, messages)

        assert payload is None
        roles = [m["role"] for m in updated]
        assert "tool" in roles
        # Last message is the final assistant reply
        assert updated[-1]["role"] == "assistant"
        assert updated[-1]["content"] == "Found 1 listing."

    def test_tool_result_is_valid_json(self):
        """Tool result messages always contain valid JSON content."""
        sample_resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)
        tool_call = _make_tool_call_reply(
            "rental_search",
            {"filters": {"min_bedrooms": 2, "location": "Vancouver"}},
        )
        final = _make_final_reply("Done.")

        with patch("rental_search_agent.client.search", return_value=sample_resp):
            client, model = _make_client(tool_call, final)
            messages = _base_messages() + [{"role": "user", "content": "Find 2 bed"}]
            updated, _, _ = run_agent_step(client, model, messages)

        tool_msgs = [m for m in updated if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        for tm in tool_msgs:
            parsed = json.loads(tm["content"])
            assert isinstance(parsed, (dict, list))


# ---------------------------------------------------------------------------
# Tests: ask_user pauses the loop
# ---------------------------------------------------------------------------

class TestRunAgentStepAskUser:
    def test_ask_user_returns_payload_and_pauses(self):
        """When LLM calls ask_user, run_agent_step returns (messages, payload, listing_state) immediately."""
        tool_call = _make_tool_call_reply(
            "ask_user",
            {"prompt": "Which listing?", "choices": ["[1] 123 Main", "[2] 456 Oak"], "allow_multiple": False},
        )
        client, model = _make_client(tool_call)
        messages = _base_messages() + [{"role": "user", "content": "Show listings"}]

        updated, payload, _ = run_agent_step(client, model, messages)

        assert payload is not None
        assert payload["prompt"] == "Which listing?"
        assert payload["choices"] == ["[1] 123 Main", "[2] 456 Oak"]
        assert payload["allow_multiple"] is False
        assert "tool_call_id" in payload

    def test_ask_user_payload_contains_tool_call_id(self):
        tool_call = _make_tool_call_reply(
            "ask_user",
            {"prompt": "Confirm?", "choices": ["Yes", "No"], "allow_multiple": False},
            call_id="call-ask-123",
        )
        client, model = _make_client(tool_call)
        messages = _base_messages() + [{"role": "user", "content": "Go ahead"}]

        _, payload, _ = run_agent_step(client, model, messages)

        assert payload["tool_call_id"] == "call-ask-123"

    def test_messages_before_ask_user_are_included(self):
        """Messages returned alongside ask_user payload include the assistant tool_calls message."""
        tool_call = _make_tool_call_reply(
            "ask_user",
            {"prompt": "Choose?", "choices": ["A", "B"], "allow_multiple": False},
        )
        client, model = _make_client(tool_call)
        messages = _base_messages() + [{"role": "user", "content": "help"}]

        updated, payload, _ = run_agent_step(client, model, messages)

        assert payload is not None
        # The updated messages should include the assistant message that issued the tool call
        assistant_msgs = [m for m in updated if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_msgs) == 1


# ---------------------------------------------------------------------------
# Tests: filter_listings uses enriched master when available
# ---------------------------------------------------------------------------

class TestRunAgentStepFilterSource:
    def test_filter_listings_uses_enriched_master_over_raw(self):
        """When enriched master exists in history, filter_listings uses it (not raw search)."""
        enriched_listings = [
            sample_listing(id="e-1", bedrooms=2).model_dump() | {"proximity": {"Downtown Vancouver|drive": {"distance_km": 5.0, "duration_min": 10.0}}},
            sample_listing(id="e-2", bedrooms=1).model_dump() | {"proximity": {"Downtown Vancouver|drive": None}},
        ]
        raw_listings = [sample_listing(id="r-1", bedrooms=3).model_dump()]

        # History: a rental_search result followed by an enriched result
        messages = _base_messages() + [
            {"role": "user", "content": "Search"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "rental_search", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": json.dumps({"listings": raw_listings, "total_count": 1})},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc2", "type": "function", "function": {"name": "enrich_listings_with_proximity", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc2", "content": json.dumps({"listings": enriched_listings, "total_count": 2})},
        ]

        # Now LLM issues a filter_listings call → should filter from enriched (2 listings)
        tool_call = _make_tool_call_reply(
            "filter_listings",
            {"min_bedrooms": 2},
            call_id="tc3",
        )
        final = _make_final_reply("Filtered.")

        client, model = _make_client(tool_call, final)
        updated, payload, _ = run_agent_step(client, model, messages)

        assert payload is None
        # Find the tool result for filter_listings
        filter_result = None
        for m in updated:
            if m.get("role") == "tool" and m.get("tool_call_id") == "tc3":
                filter_result = json.loads(m["content"])
                break
        assert filter_result is not None
        # Should have filtered from 2 enriched listings (only bedrooms>=2 → 1 result: e-1)
        assert filter_result["total_count"] == 1
        assert filter_result["listings"][0]["id"] == "e-1"


# ---------------------------------------------------------------------------
# Tests: auto-call draft_viewing_plan after calendar_get_available_slots
# ---------------------------------------------------------------------------

class TestRunAgentStepAutoDraftViewingPlan:
    def test_auto_drafts_viewing_plan_when_llm_forgets(self):
        """After calendar_get_available_slots, if LLM returns no tool calls and slots + selected
        listings exist in history, run_agent_step auto-invokes draft_viewing_plan."""
        slots = sample_available_slots(3)
        listing = sample_listing(id="mls-001")
        listing_dict = listing.model_dump()

        # Build history where:
        # - rental_search result exists with listing
        # - ask_user result has selected=[choice with id]
        # - calendar_get_available_slots result has slots
        # The last assistant tool_calls message names calendar_get_available_slots
        from rental_search_agent.agent import build_approval_choices
        choices = build_approval_choices([listing])
        selected_choice = choices[0]

        messages = _base_messages() + [
            {"role": "user", "content": "Find listings"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc-search", "type": "function", "function": {"name": "rental_search", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc-search", "content": json.dumps({"listings": [listing_dict], "total_count": 1})},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc-ask", "type": "function", "function": {"name": "ask_user", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc-ask", "content": json.dumps({"selected": [selected_choice]})},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc-slots", "type": "function", "function": {"name": "calendar_get_available_slots", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc-slots", "content": json.dumps({"slots": slots})},
        ]

        # LLM returns a plain reply (no draft_viewing_plan call)
        final = _make_final_reply("I'll help you book viewings.")

        client, model = _make_client(final, _make_final_reply("Plan created."))
        updated, payload, _ = run_agent_step(client, model, messages)

        assert payload is None
        # There should be a tool message whose content contains "entries" (from auto-drafted plan)
        tool_msgs = [m for m in updated if m.get("role") == "tool"]
        entries_msgs = [m for m in tool_msgs if "entries" in (m.get("content") or "")]
        assert len(entries_msgs) >= 1
