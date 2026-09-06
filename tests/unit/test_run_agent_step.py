"""Unit tests for client.run_agent_step LLM loop."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.client import run_agent_step, run_agent_step_events
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

    def test_ask_user_alone_in_its_turn_preserves_display_source_from_earlier_turn(self):
        """Regression test: models that never batch more than one tool call per LLM turn (e.g.
        gemini-3.8-flash) pause on ask_user as the *sole* tool call of a fresh run_agent_step
        invocation, in a later turn than the filter/score/search call that actually set the
        displayed listings. Before the fix, listing_state["display_source"] was built from a
        round-local variable that resets to None every turn, so it went None as soon as any
        non-display tool (like ask_user) became the most recent call — causing the Streamlit UI
        to skip updating (and eventually show no) results table/map after the agent asked to
        proceed with booking. display_source must instead be reconstructed from the full message
        history so it still reports the last display-setting tool ("filter" here)."""
        listings = [l.model_dump() for l in sample_listings(2)]

        # History from an *earlier*, already-completed run_agent_step call: a filter_listings
        # result is the most recent display-setting tool in history.
        history = _base_messages() + [
            {"role": "user", "content": "2 bed in Vancouver"},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "tc-filter", "type": "function", "function": {"name": "filter_listings", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc-filter", "content": json.dumps({"listings": listings, "total_count": 2})},
        ]

        # A *new* run_agent_step invocation (e.g. after the user answered a previous ask_user
        # form) whose first and only LLM tool call this turn is ask_user.
        tool_call = _make_tool_call_reply(
            "ask_user",
            {"prompt": "Book a viewing?", "choices": ["Yes", "No"], "allow_multiple": False},
        )
        client, model = _make_client(tool_call)

        _, payload, listing_state = run_agent_step(client, model, history)

        assert payload is not None
        assert listing_state is not None
        assert listing_state["display_source"] == "filter"
        assert listing_state["display_list"] == listings


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


# ---------------------------------------------------------------------------
# Helpers to build mock streaming OpenAI responses (for run_agent_step_events(stream=True))
# ---------------------------------------------------------------------------

def _make_content_delta_chunk(content: str) -> MagicMock:
    """One streamed chunk carrying a text delta and no tool call info."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _make_tool_call_delta_chunk(index: int, call_id: str | None = None, name: str | None = None, arguments: str | None = None) -> MagicMock:
    """One streamed chunk carrying a (partial) tool-call delta at the given index."""
    tc_delta = MagicMock()
    tc_delta.index = index
    tc_delta.id = call_id
    if name is not None or arguments is not None:
        fn = MagicMock()
        fn.name = name
        fn.arguments = arguments
        tc_delta.function = fn
    else:
        tc_delta.function = None

    delta = MagicMock()
    delta.content = None
    delta.tool_calls = [tc_delta]
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _make_streaming_client(*chunk_streams) -> tuple[MagicMock, str]:
    """Build a mock OpenAI client whose chat.completions.create(..., stream=True) returns each
    chunk_streams entry (an iterable of chunks) in sequence."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = list(chunk_streams)
    return mock_client, "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Tests: run_agent_step_events(stream=True) — tool status + streamed text events
# ---------------------------------------------------------------------------

class TestRunAgentStepEventsStreaming:
    def test_streams_text_deltas_for_final_reply(self):
        """A plain streamed reply (no tool calls) yields text_delta events in order, then done
        with the fully-assembled content in the final assistant message."""
        stream = [_make_content_delta_chunk("Hello, "), _make_content_delta_chunk("world!")]
        client, model = _make_streaming_client(stream)
        messages = _base_messages() + [{"role": "user", "content": "Hi"}]

        events = list(run_agent_step_events(client, model, messages, stream=True))

        text_deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
        assert text_deltas == ["Hello, ", "world!"]
        done = [e for e in events if e["type"] == "done"][0]
        assert done["messages"][-1]["role"] == "assistant"
        assert done["messages"][-1]["content"] == "Hello, world!"
        assert done["ask_user_payload"] is None

    def test_tool_call_emits_start_and_end_around_execution(self):
        """A streamed tool call (built from partial deltas) yields tool_start before and
        tool_end (ok=True) after the tool runs, with a friendly label and matching seq."""
        rental_search_stream = [
            _make_tool_call_delta_chunk(0, call_id="call-1", name="rental_search", arguments='{"filters": '),
            _make_tool_call_delta_chunk(0, arguments='{"min_bedrooms": 2, "location": "Vancouver"}}'),
        ]
        final_stream = [_make_content_delta_chunk("Found some listings.")]
        sample_resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)

        with patch("rental_search_agent.client.search", return_value=sample_resp):
            client, model = _make_streaming_client(rental_search_stream, final_stream)
            messages = _base_messages() + [{"role": "user", "content": "Find 2 bed in Vancouver"}]
            events = list(run_agent_step_events(client, model, messages, stream=True))

        types = [e["type"] for e in events]
        start_idx = types.index("tool_start")
        end_idx = types.index("tool_end")
        assert start_idx < end_idx
        assert events[start_idx]["name"] == "rental_search"
        assert events[start_idx]["label"] == "Searching for listings..."
        assert events[end_idx]["ok"] is True
        assert events[end_idx]["seq"] == events[start_idx]["seq"]
        # Text from the final (post-tool-call) reply still streams through.
        text_deltas = [e["delta"] for e in events if e["type"] == "text_delta"]
        assert text_deltas == ["Found some listings."]
        done = [e for e in events if e["type"] == "done"][0]
        assert done["messages"][-1]["content"] == "Found some listings."

    def test_ask_user_yields_no_tool_status_and_stops_at_done(self):
        """ask_user has no TOOL_STATUS_LABELS entry, so it never emits tool_start/tool_end; the
        step still ends with a single done event carrying the ask_user_payload."""
        ask_user_stream = [
            _make_tool_call_delta_chunk(
                0,
                call_id="call-ask",
                name="ask_user",
                arguments=json.dumps({"prompt": "Which listing?", "choices": ["A", "B"], "allow_multiple": False}),
            ),
        ]
        client, model = _make_streaming_client(ask_user_stream)
        messages = _base_messages() + [{"role": "user", "content": "Show listings"}]

        events = list(run_agent_step_events(client, model, messages, stream=True))

        assert not any(e["type"] in ("tool_start", "tool_end") for e in events)
        assert [e["type"] for e in events] == ["round_start", "done"]
        done = [e for e in events if e["type"] == "done"][0]
        assert done["ask_user_payload"]["tool_call_id"] == "call-ask"
        assert done["ask_user_payload"]["prompt"] == "Which listing?"

    def test_malformed_tool_call_stream_falls_back_to_non_streaming(self):
        """If the streamed tool-call arguments never parse as valid JSON (simulating a provider
        that streams tool calls inconsistently), the generator falls back to a single
        non-streaming call and still produces a usable result."""
        broken_stream = [
            _make_tool_call_delta_chunk(0, call_id="call-1", name="rental_search", arguments="{not valid json"),
        ]
        fallback_reply = _make_tool_call_reply(
            "rental_search", {"min_bedrooms": 2, "location": "Vancouver"}, call_id="call-2"
        )
        final_stream = [_make_content_delta_chunk("Done.")]
        sample_resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)

        with patch("rental_search_agent.client.search", return_value=sample_resp):
            client, model = _make_streaming_client(broken_stream, fallback_reply, final_stream)
            messages = _base_messages() + [{"role": "user", "content": "Find 2 bed in Vancouver"}]
            events = list(run_agent_step_events(client, model, messages, stream=True))

        done = [e for e in events if e["type"] == "done"][0]
        assert done["ask_user_payload"] is None
        assert done["messages"][-1]["content"] == "Done."

    def test_partial_text_before_malformed_tool_call_is_reset_not_duplicated(self):
        """If the provider streams some assistant text before sending malformed tool-call
        arguments, the fallback must emit a text_reset before its own text_delta, so a consumer
        accumulating text_delta into one string discards the partial text instead of
        concatenating it with the fallback's (full, authoritative) text."""
        broken_stream = [
            _make_content_delta_chunk("Sure, let me "),
            _make_tool_call_delta_chunk(0, call_id="call-1", name="rental_search", arguments="{not valid json"),
        ]
        fallback_reply = _make_tool_call_reply(
            "rental_search", {"min_bedrooms": 2, "location": "Vancouver"}, call_id="call-2"
        )
        final_stream = [_make_content_delta_chunk("Done.")]
        sample_resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)

        with patch("rental_search_agent.client.search", return_value=sample_resp):
            client, model = _make_streaming_client(broken_stream, fallback_reply, final_stream)
            messages = _base_messages() + [{"role": "user", "content": "Find 2 bed in Vancouver"}]
            events = list(run_agent_step_events(client, model, messages, stream=True))

        # The partial "Sure, let me " text_delta arrives, then a text_reset, then no further
        # text_delta until the (unrelated, next-round) final "Done." reply.
        types_and_deltas = [(e["type"], e.get("delta")) for e in events if e["type"] in ("text_delta", "text_reset")]
        assert types_and_deltas[0] == ("text_delta", "Sure, let me ")
        assert types_and_deltas[1] == ("text_reset", None)
        assert types_and_deltas[2] == ("text_delta", "Done.")
        # A consumer that resets its accumulator on text_reset (like the Streamlit UI helper)
        # ends up with just "Done.", not "Sure, let me Done.".
        acc = ""
        for etype, delta in types_and_deltas:
            if etype == "text_reset":
                acc = ""
            else:
                acc += delta
        assert acc == "Done."
