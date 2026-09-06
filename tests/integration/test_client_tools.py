"""Integration tests for client tool runner and helpers."""

import json
from unittest.mock import patch

import pytest

from rental_search_agent.client import (
    TOOLS,
    _get_active_search_criteria_from_messages,
    _get_current_listings_from_messages,
    _get_enriched_master_from_messages,
    _get_parsed_proximity_rules_from_messages,
    _get_viewing_plan_from_messages,
    _infer_last_sort_by,
    _listing_state_from_messages,
    _with_display_rank,
    run_tool,
)
from rental_search_agent.server import draft_viewing_plan
from rental_search_agent.models import RentalSearchResponse
from tests.fixtures.sample_data import (
    sample_available_slots,
    sample_listing,
    sample_listings,
    sample_listings_with_coords,
)


def _assistant_tool_call_msg(name: str, arguments: dict | None = None, tc_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": tc_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {})}}
        ],
    }


class TestInferLastSortBy:
    """Regression tests for the Bugbot finding that the UI's default match-score sort
    was silently overriding an agent's explicit non-score sort_by (e.g. price/proximity),
    since the UI had no signal for whether an explicit sort was already active."""

    def test_no_tool_calls_returns_none(self):
        assert _infer_last_sort_by([{"role": "user", "content": "hi"}]) is None

    def test_filter_listings_sort_by_is_tracked(self):
        messages = [_assistant_tool_call_msg("filter_listings", {"sort_by": "price"})]
        assert _infer_last_sort_by(messages) == "price"

    def test_filter_listings_without_sort_by_resets_to_none(self):
        messages = [
            _assistant_tool_call_msg("filter_listings", {"sort_by": "price"}),
            _assistant_tool_call_msg("filter_listings", {"min_bedrooms": 2}),
        ]
        assert _infer_last_sort_by(messages) is None

    def test_score_listings_by_preferences_implies_semantic_score(self):
        messages = [_assistant_tool_call_msg("score_listings_by_preferences", {"preferences_text": "balcony"})]
        assert _infer_last_sort_by(messages) == "semantic_score"

    def test_rental_search_resets_to_none(self):
        messages = [
            _assistant_tool_call_msg("filter_listings", {"sort_by": "price"}),
            _assistant_tool_call_msg("rental_search", {"min_bedrooms": 2, "location": "Vancouver"}),
        ]
        assert _infer_last_sort_by(messages) is None

    def test_enrich_does_not_change_sort(self):
        # enrich_listings_with_proximity does not reorder listings, so it must not
        # clobber a previously-active explicit sort.
        messages = [
            _assistant_tool_call_msg("score_listings_by_preferences", {"preferences_text": "balcony"}),
            _assistant_tool_call_msg("enrich_listings_with_proximity", {"rules": [], "geocoded_refs": []}),
        ]
        assert _infer_last_sort_by(messages) == "semantic_score"

    def test_listing_state_from_messages_includes_last_sort_by(self):
        messages = [_assistant_tool_call_msg("filter_listings", {"sort_by": "proximity"})]
        state = _listing_state_from_messages(messages)
        assert state["last_sort_by"] == "proximity"


class TestFilterListingsToolSchema:
    def test_sort_by_enum_includes_listing_age_hours(self):
        # Regression test (Bugbot finding): flow instructions and filtering.SORTABLE_ATTRS
        # both support sorting by listing_age_hours, but the tool schema exposed to the
        # LLM previously omitted it from the enum, making the documented sort unreachable.
        filter_tool = next(t for t in TOOLS if t["function"]["name"] == "filter_listings")
        sort_by_enum = filter_tool["function"]["parameters"]["properties"]["sort_by"]["enum"]
        assert "listing_age_hours" in sort_by_enum


class TestWithDisplayRank:
    def test_assigns_one_based_rank_in_order(self):
        listings = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        result = _with_display_rank(listings)
        assert [d["rank"] for d in result] == [1, 2, 3]
        assert [d["id"] for d in result] == ["a", "b", "c"]

    def test_does_not_mutate_input(self):
        listings = [{"id": "a"}]
        _with_display_rank(listings)
        assert "rank" not in listings[0]

    def test_empty_list(self):
        assert _with_display_rank([]) == []


class TestRunTool:
    def test_ask_user_returns_request_payload(self):
        result = run_tool(
            "ask_user",
            {"prompt": "Choose?", "choices": ["A", "B"], "allow_multiple": False},
        )
        data = json.loads(result)
        assert data["request_user_input"] is True
        assert data["prompt"] == "Choose?"
        assert data["choices"] == ["A", "B"]

    def test_filter_listings_with_current_listings(self):
        listings = [l.model_dump() for l in sample_listings(3)]
        result = run_tool(
            "filter_listings",
            {"sort_by": "price", "ascending": True},
            current_listings=listings,
        )
        data = json.loads(result)
        assert "listings" in data
        assert len(data["listings"]) == 3

    def test_filter_listings_sets_rank_matching_sorted_order(self):
        # rank must reflect the *post-sort* order returned to the LLM, not the
        # pre-sort input order, so "listing 1" always means the first row shown.
        listings = [l.model_dump() for l in sample_listings(3)]
        result = run_tool(
            "filter_listings",
            {"sort_by": "price", "ascending": False},
            current_listings=listings,
        )
        data = json.loads(result)
        ranks = [lst["rank"] for lst in data["listings"]]
        assert ranks == [1, 2, 3]
        prices = [lst["price"] for lst in data["listings"]]
        assert prices == sorted(prices, reverse=True)

    def test_filter_listings_without_listings_returns_error(self):
        result = run_tool("filter_listings", {"sort_by": "price"})
        data = json.loads(result)
        assert "error" in data
        assert "Run a search first" in data["error"]

    def test_summarize_listings_with_listings(self):
        listings = [l.model_dump() for l in sample_listings(2)]
        result = run_tool("summarize_listings", {}, current_listings=listings)
        data = json.loads(result)
        assert data["count"] == 2
        assert "price" in data

    def test_summarize_listings_without_listings_returns_error(self):
        result = run_tool("summarize_listings", {})
        data = json.loads(result)
        assert "error" in data

    def test_simulate_viewing_request_valid(self):
        result = run_tool(
            "simulate_viewing_request",
            {
                "listing_url": "https://x.com/1",
                "timeslot": "Tue 6pm",
                "user_details": {"name": "Jane", "email": "j@x.com"},
            },
        )
        data = json.loads(result)
        assert "summary" in data
        assert "Viewing request [simulated]" in data["summary"]

    def test_simulate_viewing_request_invalid_returns_error(self):
        result = run_tool(
            "simulate_viewing_request",
            {
                "listing_url": "https://x.com",
                "timeslot": "Tue",
                "user_details": {"email": "j@x.com"},
            },
        )
        data = json.loads(result)
        assert "error" in data

    def test_rental_search_mocked(self):
        resp = RentalSearchResponse(listings=[sample_listing()], total_count=1)
        with patch("rental_search_agent.client.search", return_value=resp):
            result = run_tool(
                "rental_search",
                {"filters": {"min_bedrooms": 2, "location": "Vancouver"}},
            )
            data = json.loads(result)
            assert data["total_count"] == 1
            assert len(data["listings"]) == 1
            assert data["listings"][0]["rank"] == 1

    def test_unknown_tool_returns_error(self):
        result = run_tool("unknown_tool", {})
        data = json.loads(result)
        assert "error" in data
        assert "Unknown tool" in data["error"]

    def test_draft_viewing_plan_valid(self):
        listings = sample_listings_with_coords()
        slots = sample_available_slots(3)
        result = run_tool(
            "draft_viewing_plan",
            {"listings": listings, "available_slots": slots},
        )
        data = json.loads(result)
        assert "entries" in data
        assert len(data["entries"]) == 3

    def test_draft_viewing_plan_not_enough_slots_returns_error(self):
        listings = sample_listings_with_coords()
        slots = sample_available_slots(2)
        result = run_tool(
            "draft_viewing_plan",
            {"listings": listings, "available_slots": slots},
        )
        data = json.loads(result)
        assert "error" in data
        assert "Not enough slots" in data["error"]

    def test_modify_viewing_plan_with_context(self):
        plan = draft_viewing_plan(sample_listings_with_coords(), sample_available_slots(3))
        slots = sample_available_slots(3)
        result = run_tool(
            "modify_viewing_plan",
            {"remove": ["mls-002"]},
            current_plan_entries=plan["entries"],
            available_slots=slots,
        )
        data = json.loads(result)
        assert "entries" in data
        assert len(data["entries"]) == 2
        ids = [e["listing_id"] for e in data["entries"]]
        assert "mls-001" in ids
        assert "mls-003" in ids
        assert "mls-002" not in ids

    def test_modify_viewing_plan_no_plan_returns_error(self):
        result = run_tool(
            "modify_viewing_plan",
            {"remove": ["mls-002"]},
        )
        data = json.loads(result)
        assert "error" in data
        assert "No current viewing plan" in data["error"]

    def test_calendar_get_available_slots_mocked(self):
        with patch("rental_search_agent.client.calendar_get_available_slots") as m:
            m.return_value = {"slots": sample_available_slots(2)}
            result = run_tool(
                "calendar_get_available_slots",
                {
                    "preferred_times": "weekday evenings 6-8pm",
                    "date_range_start": "2026-02-25T00:00:00",
                    "date_range_end": "2026-03-05T00:00:00",
                },
            )
            data = json.loads(result)
            assert "slots" in data
            assert len(data["slots"]) == 2

    def test_calendar_get_available_slots_auth_error_returns_error(self):
        with patch("rental_search_agent.client.calendar_get_available_slots") as m:
            m.side_effect = ValueError("credentials not found")
            result = run_tool(
                "calendar_get_available_slots",
                {
                    "preferred_times": "weekday evenings",
                    "date_range_start": "2026-02-25T00:00:00",
                    "date_range_end": "2026-03-05T00:00:00",
                },
            )
            data = json.loads(result)
            assert "error" in data
            assert "credentials" in data["error"]

    def test_calendar_create_event_mocked(self):
        with patch("rental_search_agent.client.calendar_create_event") as m:
            m.return_value = {"id": "ev123", "htmlLink": "https://calendar.google.com/ev123", "summary": "Viewing"}
            result = run_tool(
                "calendar_create_event",
                {
                    "summary": "Rental viewing: 123 Main St",
                    "start_datetime": "2026-02-25T18:00:00",
                    "end_datetime": "2026-02-25T19:00:00",
                },
            )
            data = json.loads(result)
            assert data["id"] == "ev123"
            assert "Viewing" in data["summary"]

    def test_calendar_update_event_mocked(self):
        with patch("rental_search_agent.client.calendar_update_event") as m:
            m.return_value = {
                "id": "ev123",
                "htmlLink": "https://calendar.google.com/ev123",
                "summary": "Updated viewing",
            }
            result = run_tool(
                "calendar_update_event",
                {
                    "event_id": "ev123",
                    "summary": "Updated viewing",
                    "start_datetime": "2026-02-25T18:30:00",
                    "end_datetime": "2026-02-25T19:30:00",
                },
            )
            data = json.loads(result)
            assert data["id"] == "ev123"
            assert data["summary"] == "Updated viewing"
            m.assert_called_once()
            called_args, called_kwargs = m.call_args
            assert called_kwargs["event_id"] == "ev123"
            assert called_kwargs["summary"] == "Updated viewing"

    def test_calendar_delete_event_mocked(self):
        with patch("rental_search_agent.client.calendar_delete_event") as m:
            m.return_value = {"deleted": "ev123"}
            result = run_tool(
                "calendar_delete_event",
                {"event_id": "ev123"},
            )
            data = json.loads(result)
            assert data["deleted"] == "ev123"
            m.assert_called_once_with("ev123")

    def test_calendar_list_events_mocked(self):
        events = [
            {"id": "ev1", "summary": "Viewing 1"},
            {"id": "ev2", "summary": "Viewing 2"},
        ]
        with patch("rental_search_agent.client.calendar_list_events") as m:
            m.return_value = events
            result = run_tool(
                "calendar_list_events",
                {
                    "time_min": "2026-02-25T00:00:00",
                    "time_max": "2026-02-26T00:00:00",
                },
            )
            data = json.loads(result)
            assert isinstance(data, list)
            assert len(data) == 2
            assert data[0]["id"] == "ev1"
            assert data[1]["id"] == "ev2"

    def test_calendar_create_event_validation_error(self):
        result = run_tool(
            "calendar_create_event",
            {"summary": "Rental viewing: 123 Main St"},
        )
        data = json.loads(result)
        assert "error" in data
        assert "start_datetime" in data["error"]
        assert "end_datetime" in data["error"]


class TestGetCurrentListingsFromMessages:
    def test_extracts_from_tool_result(self):
        listings = [{"id": "1", "address": "123 Main St"}]
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": json.dumps({"listings": listings})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == listings

    def test_most_recent_wins(self):
        old = [{"id": "old"}]
        new = [{"id": "new"}]
        messages = [
            {"role": "tool", "content": json.dumps({"listings": old})},
            {"role": "assistant", "content": "x"},
            {"role": "tool", "content": json.dumps({"listings": new})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == new

    def test_ignores_ask_user_results(self):
        messages = [
            {"role": "tool", "content": json.dumps({"answer": "Yes"})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == []

    def test_ignores_error_results(self):
        messages = [
            {"role": "tool", "content": json.dumps({"error": "search failed"})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == []

    def test_error_and_listings_listings_take_precedence(self):
        """When a tool result has both 'error' and 'listings', listings are returned (listings take precedence)."""
        listings = [{"id": "1", "address": "123 Main St"}]
        messages = [
            {
                "role": "tool",
                "content": json.dumps({"error": "partial failure", "listings": listings}),
            },
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == listings

    def test_malformed_json_skipped(self):
        listings = [{"id": "1"}]
        messages = [
            {"role": "tool", "content": "not json"},
            {"role": "tool", "content": json.dumps({"listings": listings})},
        ]
        result = _get_current_listings_from_messages(messages)
        assert result == listings


def _tool_result_msg(tc_id: str, content: dict) -> dict:
    return {"role": "tool", "tool_call_id": tc_id, "content": json.dumps(content)}


class TestGetEnrichedMasterFromMessages:
    """Regression tests for the bug where match scores disappeared after filtering/sorting
    in a later LLM round-trip: _get_enriched_master_from_messages previously only looked at
    the most recent enrich_listings_with_proximity result, ignoring score_listings_by_preferences.
    When score ran in one LLM round-trip and filter_listings (or another score/filter) ran in a
    *later* round-trip, this function recomputes "enriched_master" purely from message history
    (see run_agent_step_events / _listing_state_from_messages) and must treat
    score_listings_by_preferences as an equally valid master source, or the recomputed master
    reverts to the pre-scoring enrich result and silently drops every semantic_score.
    """

    def test_returns_enrich_result_when_only_enrich_ran(self):
        enriched = [{"id": "a", "proximity": {"transit|walk": {"duration_min": 3}}}]
        messages = [
            _assistant_tool_call_msg("enrich_listings_with_proximity", {}, tc_id="e1"),
            _tool_result_msg("e1", {"listings": enriched}),
        ]
        assert _get_enriched_master_from_messages(messages) == enriched

    def test_returns_score_result_when_only_score_ran(self):
        scored = [{"id": "a", "semantic_score": 0.8}]
        messages = [
            _assistant_tool_call_msg("score_listings_by_preferences", {"preferences_text": "balcony"}, tc_id="s1"),
            _tool_result_msg("s1", {"listings": scored}),
        ]
        assert _get_enriched_master_from_messages(messages) == scored

    def test_score_after_enrich_in_separate_round_trip_wins(self):
        """Core regression: enrich then score in a LATER round-trip. Previously this
        returned the stale pre-score enrich listings (semantic_score=None); it must now
        return the scored listings."""
        enriched = [{"id": "a", "proximity": {"transit|walk": {"duration_min": 3}}, "semantic_score": None}]
        scored = [{"id": "a", "proximity": {"transit|walk": {"duration_min": 3}}, "semantic_score": 0.75}]
        messages = [
            _assistant_tool_call_msg("enrich_listings_with_proximity", {}, tc_id="e1"),
            _tool_result_msg("e1", {"listings": enriched}),
            _assistant_tool_call_msg("score_listings_by_preferences", {"preferences_text": "balcony"}, tc_id="s1"),
            _tool_result_msg("s1", {"listings": scored}),
        ]
        result = _get_enriched_master_from_messages(messages)
        assert result == scored
        assert result[0]["semantic_score"] == 0.75

    def test_enrich_after_score_in_separate_round_trip_wins(self):
        """Reverse order: score then enrich later must return the enrich result (which, in
        the real system, inherits semantic_score via the Listing model round-trip)."""
        scored = [{"id": "a", "semantic_score": 0.75}]
        enriched = [{"id": "a", "semantic_score": 0.75, "proximity": {"transit|walk": {"duration_min": 3}}}]
        messages = [
            _assistant_tool_call_msg("score_listings_by_preferences", {"preferences_text": "balcony"}, tc_id="s1"),
            _tool_result_msg("s1", {"listings": scored}),
            _assistant_tool_call_msg("enrich_listings_with_proximity", {}, tc_id="e1"),
            _tool_result_msg("e1", {"listings": enriched}),
        ]
        assert _get_enriched_master_from_messages(messages) == enriched

    def test_new_rental_search_resets_master(self):
        scored = [{"id": "a", "semantic_score": 0.75}]
        new_search = [{"id": "b"}]
        messages = [
            _assistant_tool_call_msg("score_listings_by_preferences", {"preferences_text": "balcony"}, tc_id="s1"),
            _tool_result_msg("s1", {"listings": scored}),
            _assistant_tool_call_msg("rental_search", {"location": "Vancouver"}, tc_id="r1"),
            _tool_result_msg("r1", {"listings": new_search}),
        ]
        assert _get_enriched_master_from_messages(messages) == []

    def test_no_enrich_or_score_returns_empty(self):
        messages = [
            _assistant_tool_call_msg("rental_search", {"location": "Vancouver"}, tc_id="r1"),
            _tool_result_msg("r1", {"listings": [{"id": "a"}]}),
        ]
        assert _get_enriched_master_from_messages(messages) == []


class TestGetActiveSearchCriteriaFromMessages:
    """Regression/feature tests for the search_criteria_to_text_blob wiring: the query used
    for score_listings_by_preferences / analyze_listing_preferences should mirror the
    listing_to_text_blob structure (bed/bath/sqft/price/location), reconstructed
    deterministically from message history rather than relying on the LLM to re-supply it."""

    def test_only_rental_search_ran(self):
        messages = [
            _assistant_tool_call_msg(
                "rental_search",
                {
                    "filters": {
                        "min_bedrooms": 3,
                        "max_bedrooms": 3,
                        "location": "Metrotown, Burnaby, BC",
                        "listing_type": "for_sale",
                        "price_max": 1000000,
                    }
                },
            )
        ]
        criteria = _get_active_search_criteria_from_messages(messages)
        assert criteria["location"] == "Metrotown, Burnaby, BC"
        assert criteria["listing_type"] == "for_sale"
        assert criteria["min_bedrooms"] == 3
        assert criteria["max_bedrooms"] == 3
        assert criteria["price_max"] == 1000000
        assert criteria["price_min"] is None

    def test_filter_listings_after_search_overrides_structural_criteria_only(self):
        messages = [
            _assistant_tool_call_msg(
                "rental_search",
                {"filters": {"min_bedrooms": 3, "location": "Burnaby, BC", "price_max": 1000000}},
                tc_id="s1",
            ),
            _assistant_tool_call_msg(
                "filter_listings", {"price_max": 900000, "min_bedrooms": 3, "max_bedrooms": 3}, tc_id="f1"
            ),
        ]
        criteria = _get_active_search_criteria_from_messages(messages)
        # location/listing_type only ever come from rental_search (filter_listings has no such args).
        assert criteria["location"] == "Burnaby, BC"
        # Structural criteria come from the later, more specific filter_listings call.
        assert criteria["price_max"] == 900000
        assert criteria["max_bedrooms"] == 3

    def test_new_rental_search_resets_prior_filter_narrowing(self):
        messages = [
            _assistant_tool_call_msg("rental_search", {"filters": {"min_bedrooms": 3, "location": "Burnaby, BC"}}, tc_id="s1"),
            _assistant_tool_call_msg("filter_listings", {"price_max": 900000}, tc_id="f1"),
            _assistant_tool_call_msg("rental_search", {"filters": {"min_bedrooms": 2, "location": "Vancouver, BC"}}, tc_id="s2"),
        ]
        criteria = _get_active_search_criteria_from_messages(messages)
        assert criteria["location"] == "Vancouver, BC"
        assert criteria["min_bedrooms"] == 2
        assert criteria["price_max"] is None

    def test_no_tool_calls_returns_all_none(self):
        criteria = _get_active_search_criteria_from_messages([])
        assert criteria["location"] is None
        assert criteria["listing_type"] is None
        for key in ("min_bedrooms", "max_bedrooms", "min_bathrooms", "max_bathrooms", "min_sqft", "max_sqft", "price_min", "price_max"):
            assert criteria[key] is None

    def test_proximity_only_filter_listings_call_does_not_blank_out_earlier_structural_criteria(self):
        """Regression: when 3b (structural narrow) is skipped because rental_search already
        covers everything, the first filter_listings call may be step 4p's proximity-only call
        (proximity_rules + sort_by="proximity", no bed/bath/price args at all). Treating that
        call as wholesale-authoritative for structural criteria would wrongly blank out the
        bedrooms/price already established by rental_search — verified live via a traced CLI
        run where the resulting embedding query was missing "3 bedrooms"/"$1000000" entirely."""
        messages = [
            _assistant_tool_call_msg(
                "rental_search",
                {
                    "filters": {
                        "min_bedrooms": 3,
                        "max_bedrooms": 3,
                        "location": "Metrotown, Burnaby, BC",
                        "listing_type": "for_sale",
                        "price_max": 1000000,
                    }
                },
                tc_id="s1",
            ),
            _assistant_tool_call_msg(
                "filter_listings",
                {
                    "proximity_rules": [{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}],
                    "sort_by": "proximity",
                    "ascending": True,
                },
                tc_id="f1",
            ),
        ]
        criteria = _get_active_search_criteria_from_messages(messages)
        assert criteria["min_bedrooms"] == 3
        assert criteria["max_bedrooms"] == 3
        assert criteria["price_max"] == 1000000
        assert criteria["location"] == "Metrotown, Burnaby, BC"


class TestGetParsedProximityRulesFromMessages:
    def test_no_parse_call_returns_empty(self):
        assert _get_parsed_proximity_rules_from_messages([]) == []

    def test_returns_rules_from_call_result(self):
        rules = [{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}]
        messages = [
            _assistant_tool_call_msg("parse_proximity_preferences", {"proximity_text": "5 min walk to transit"}, tc_id="p1"),
            _tool_result_msg("p1", {"rules": rules}),
        ]
        assert _get_parsed_proximity_rules_from_messages(messages) == rules

    def test_most_recent_call_wins(self):
        messages = [
            _assistant_tool_call_msg("parse_proximity_preferences", {}, tc_id="p1"),
            _tool_result_msg("p1", {"rules": [{"location": "old", "mode": "walk", "max_minutes": 10}]}),
            _assistant_tool_call_msg("parse_proximity_preferences", {}, tc_id="p2"),
            _tool_result_msg("p2", {"rules": [{"location": "new", "mode": "drive", "max_minutes": 15}]}),
        ]
        rules = _get_parsed_proximity_rules_from_messages(messages)
        assert rules == [{"location": "new", "mode": "drive", "max_minutes": 15}]

    def test_not_reset_by_a_new_rental_search(self):
        # Proximity preferences are user-level, not tied to a particular search.
        rules = [{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}]
        messages = [
            _assistant_tool_call_msg("parse_proximity_preferences", {}, tc_id="p1"),
            _tool_result_msg("p1", {"rules": rules}),
            _assistant_tool_call_msg("rental_search", {"filters": {"min_bedrooms": 2, "location": "Toronto, ON"}}, tc_id="s1"),
        ]
        assert _get_parsed_proximity_rules_from_messages(messages) == rules


class TestRunToolScoreListingsByPreferencesQueryBlob:
    """The embedding query for score_listings_by_preferences must be built via
    search_criteria_to_text_blob (mirroring listing_to_text_blob's shape) rather than the
    bare preferences_text, using the search_criteria/proximity_rules_for_query kwargs
    threaded in from run_agent_step_events."""

    def test_uses_search_criteria_and_proximity_in_embedding_query(self):
        listings = [sample_listing()]
        captured = {}

        def fake_score(listings_arg, query_text_arg):
            captured["query"] = query_text_arg
            return [dict(listings_arg[0], semantic_score=0.5)]

        with patch("rental_search_agent.client.do_score_listings_by_preferences", side_effect=fake_score):
            run_tool(
                "score_listings_by_preferences",
                {"preferences_text": "must have balcony"},
                current_listings=listings,
                search_criteria={
                    "location": "Metrotown, Burnaby, BC",
                    "min_bedrooms": 3,
                    "max_bedrooms": 3,
                    "listing_type": "for_sale",
                    "price_max": 1000000,
                },
                proximity_rules_for_query=[{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}],
            )

        assert captured["query"] == (
            "Metrotown, Burnaby, BC 3 bedrooms, up to $1000000 list price must have balcony "
            "5 min walk to nearest transit station"
        )

    def test_appends_llm_supplied_query_text_as_extra_context(self):
        listings = [sample_listing()]
        captured = {}

        def fake_score(listings_arg, query_text_arg):
            captured["query"] = query_text_arg
            return listings_arg

        with patch("rental_search_agent.client.do_score_listings_by_preferences", side_effect=fake_score):
            run_tool(
                "score_listings_by_preferences",
                {"preferences_text": "must have balcony", "query_text": "near parks"},
                current_listings=listings,
                search_criteria={"location": "Burnaby, BC"},
            )

        assert captured["query"] == "Burnaby, BC must have balcony near parks"

    def test_falls_back_to_bare_preferences_when_no_search_criteria_given(self):
        listings = [sample_listing()]
        captured = {}

        def fake_score(listings_arg, query_text_arg):
            captured["query"] = query_text_arg
            return listings_arg

        with patch("rental_search_agent.client.do_score_listings_by_preferences", side_effect=fake_score):
            run_tool(
                "score_listings_by_preferences",
                {"preferences_text": "must have balcony"},
                current_listings=listings,
            )

        assert captured["query"] == "must have balcony"


class TestRunToolAnalyzeListingPreferencesQueryBlob:
    def test_score_query_text_enriched_but_narrative_preferences_text_unchanged(self):
        captured = {}

        def fake_analyze(listing, preferences_text, conversation_context=None, score_query_text=None):
            captured["preferences_text"] = preferences_text
            captured["score_query_text"] = score_query_text
            return {"match_score_pct": 50, "key_matches": [], "key_gaps": []}

        combined_preferences = "must have balcony\n\nProximity: 5 min walk to transit"
        with patch("rental_search_agent.client.do_analyze_listing_against_preferences", side_effect=fake_analyze):
            run_tool(
                "analyze_listing_preferences",
                {"listing": {"id": "a"}, "preferences_text": combined_preferences},
                search_criteria={"location": "Burnaby, BC", "min_bedrooms": 3, "max_bedrooms": 3, "listing_type": "for_sale"},
                # Deliberately also pass proximity rules to prove they are NOT re-added here
                # (preferences_text may already contain "Proximity: ..." per this tool's
                # existing contract, so doubling it up would skew the score).
                proximity_rules_for_query=[{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}],
            )

        # Narrative text is passed through untouched.
        assert captured["preferences_text"] == combined_preferences
        # Score query folds in structural criteria but does not duplicate proximity text.
        assert captured["score_query_text"] == "Burnaby, BC 3 bedrooms must have balcony\n\nProximity: 5 min walk to transit"
        assert "5 min walk to nearest transit station" not in captured["score_query_text"]


class TestGetViewingPlanFromMessages:
    def test_extracts_entries_from_draft_viewing_plan_result(self):
        entries = [
            {"listing_id": "a", "listing_address": "A", "listing_url": "https://a", "slot_display": "Mon", "start_datetime": "2026-02-25T18:00:00", "end_datetime": "2026-02-25T19:00:00"},
        ]
        messages = [
            {"role": "tool", "content": json.dumps({"entries": entries})},
        ]
        result = _get_viewing_plan_from_messages(messages)
        assert result == entries

    def test_most_recent_plan_wins(self):
        old_entries = [{"listing_id": "old"}]
        new_entries = [{"listing_id": "new"}]
        messages = [
            {"role": "tool", "content": json.dumps({"entries": old_entries})},
            {"role": "tool", "content": json.dumps({"entries": new_entries})},
        ]
        result = _get_viewing_plan_from_messages(messages)
        assert result == new_entries

    def test_empty_when_no_plan(self):
        messages = [{"role": "tool", "content": json.dumps({"slots": []})}]
        result = _get_viewing_plan_from_messages(messages)
        assert result == []
