"""Integration tests for MCP server tools."""

from unittest.mock import patch

import pytest

from rental_search_agent.adapter import SearchBackendError
from rental_search_agent.models import Listing, RentalSearchResponse
from rental_search_agent.server import (
    ask_user,
    calendar_create_event,
    calendar_delete_event,
    calendar_get_available_slots,
    calendar_list_events,
    calendar_update_event,
    draft_viewing_plan,
    filter_listings,
    modify_viewing_plan,
    rental_search,
    simulate_viewing_request,
    summarize_listings,
)
from tests.fixtures.sample_data import (
    sample_available_slots,
    sample_listing,
    sample_listings,
    sample_listings_with_coords,
)


class TestAskUser:
    def test_valid_payload(self):
        result = ask_user(prompt="Choose one", choices=["A", "B"], allow_multiple=False)
        assert result["request_user_input"] is True
        assert result["prompt"] == "Choose one"
        assert result["choices"] == ["A", "B"]
        assert result["allow_multiple"] is False

    def test_empty_prompt_raises(self):
        with pytest.raises(ValueError, match="prompt is required"):
            ask_user(prompt="", choices=["A"])

    def test_invalid_choices_type_raises(self):
        with pytest.raises(ValueError, match="choices must be a list"):
            ask_user(prompt="Q", choices="not a list")


class TestRentalSearch:
    def test_valid_filters_returns_response(self):
        sample = RentalSearchResponse(
            listings=[sample_listing()],
            total_count=1,
        )

        with patch("rental_search_agent.server.search", return_value=sample):
            result = rental_search(
                {"min_bedrooms": 2, "location": "Vancouver"}
            )
            assert result.total_count == 1
            assert len(result.listings) == 1
            assert result.listings[0].address == "123 Main St"

    def test_invalid_filters_raises(self):
        with pytest.raises(ValueError, match="Invalid filters"):
            rental_search({"location": "Vancouver"})

    def test_search_backend_error_becomes_value_error(self):
        with patch("rental_search_agent.server.search", side_effect=SearchBackendError("unavailable")):
            with pytest.raises(ValueError, match="unavailable"):
                rental_search({"min_bedrooms": 2, "location": "Vancouver"})


class TestFilterListings:
    def test_valid_listings_and_criteria(self):
        listings = [l.model_dump() for l in sample_listings(3)]
        result = filter_listings(
            listings,
            {"min_bedrooms": 2},
        )
        assert result.total_count == 3
        assert len(result.listings) == 3

    def test_filter_narrows_results(self):
        listings = [
            sample_listing(id="1", bedrooms=1).model_dump(),
            sample_listing(id="2", bedrooms=2).model_dump(),
            sample_listing(id="3", bedrooms=3).model_dump(),
        ]
        result = filter_listings(listings, {"min_bedrooms": 2})
        assert result.total_count == 2
        assert all(l.bedrooms >= 2 for l in result.listings)

    def test_sort_only(self):
        listings = [
            sample_listing(id="1", price=3000).model_dump(),
            sample_listing(id="2", price=2000).model_dump(),
        ]
        result = filter_listings(
            listings,
            {},
            sort_by="price",
            ascending=True,
        )
        assert result.listings[0].price == 2000
        assert result.listings[1].price == 3000

    def test_empty_listings_raises(self):
        with pytest.raises(ValueError, match="listings is required"):
            filter_listings([], {"min_bedrooms": 2})

    def test_no_criteria_and_no_sort_raises(self):
        listings = [sample_listing().model_dump()]
        with pytest.raises(ValueError, match="At least one filter criterion or sort_by"):
            filter_listings(listings, {})


class TestSummarizeListings:
    def test_valid_listings(self):
        listings = [l.model_dump() for l in sample_listings(2)]
        result = summarize_listings(listings)
        assert result["count"] == 2
        assert "price" in result
        assert "bedrooms" in result

    def test_empty_listings_raises(self):
        with pytest.raises(ValueError, match="listings is required"):
            summarize_listings([])


class TestSimulateViewingRequest:
    def test_valid_args(self):
        result = simulate_viewing_request(
            listing_url="https://example.com/listing/1",
            timeslot="Tuesday 6-8pm",
            user_details={"name": "Jane", "email": "jane@test.com"},
        )
        assert "Viewing request [simulated]" in result.summary
        assert "https://example.com/listing/1" in result.summary
        assert "Jane" in result.summary
        assert result.contact_url is not None
        assert "mailto:" in result.contact_url

    def test_empty_listing_url_raises(self):
        with pytest.raises(ValueError, match="listing_url"):
            simulate_viewing_request(
                listing_url="",
                timeslot="Tuesday",
                user_details={"name": "J", "email": "j@x.com"},
            )

    def test_empty_timeslot_raises(self):
        with pytest.raises(ValueError, match="timeslot"):
            simulate_viewing_request(
                listing_url="https://x.com",
                timeslot="",
                user_details={"name": "J", "email": "j@x.com"},
            )

    def test_invalid_user_details_raises(self):
        with pytest.raises(ValueError, match="Invalid user_details"):
            simulate_viewing_request(
                listing_url="https://x.com",
                timeslot="Tue",
                user_details={"email": "j@x.com"},
            )


class TestDraftViewingPlan:
    def test_valid_listings_and_slots(self):
        listings = sample_listings_with_coords()
        slots = sample_available_slots(3)
        result = draft_viewing_plan(listings, slots)
        assert "entries" in result
        assert len(result["entries"]) == 3
        assert result["entries"][0]["listing_id"] in ("mls-001", "mls-002", "mls-003")

    def test_more_listings_than_slots_raises(self):
        listings = sample_listings_with_coords()
        slots = sample_available_slots(2)
        with pytest.raises(ValueError, match="Not enough slots"):
            draft_viewing_plan(listings, slots)

    def test_returns_unused_slots(self):
        listings = [{"id": "a", "address": "A", "url": "https://a", "latitude": 49.28, "longitude": -123.12}]
        slots = sample_available_slots(3)
        result = draft_viewing_plan(listings, slots)
        assert "unused_slots" in result
        assert len(result["unused_slots"]) == 2


class TestModifyViewingPlan:
    def test_remove_listing(self):
        plan = draft_viewing_plan(sample_listings_with_coords(), sample_available_slots(3))
        result = modify_viewing_plan(
            plan["entries"],
            sample_available_slots(3),
            remove=["mls-002"],
        )
        assert "entries" in result
        assert len(result["entries"]) == 2
        ids = [e["listing_id"] for e in result["entries"]]
        assert "mls-001" in ids
        assert "mls-003" in ids
        assert "mls-002" not in ids
        assert "unused_slots" in result

    def test_add_listing(self):
        plan = draft_viewing_plan(
            [{"id": "a", "address": "A", "url": "https://a", "latitude": 49.28, "longitude": -123.12}],
            sample_available_slots(3),
        )
        slots = sample_available_slots(3)
        result = modify_viewing_plan(
            plan["entries"],
            slots,
            add=[
                {
                    "listing_id": "b",
                    "listing_address": "B",
                    "listing_url": "https://b",
                    "slot": slots[1],
                }
            ],
        )
        assert len(result["entries"]) == 2
        ids = [e["listing_id"] for e in result["entries"]]
        assert "a" in ids
        assert "b" in ids


class TestCalendarListEvents:
    def test_returns_events_when_mocked(self):
        with patch("rental_search_agent.server.do_calendar_list_events") as m:
            m.return_value = [
                {"id": "ev1", "summary": "Meeting", "start": {"dateTime": "2026-02-25T10:00:00"}, "end": {"dateTime": "2026-02-25T11:00:00"}},
            ]
            result = calendar_list_events("2026-02-25T00:00:00", "2026-02-26T00:00:00")
            assert "events" in result
            assert len(result["events"]) == 1
            assert result["events"][0]["id"] == "ev1"
            assert result["events"][0]["summary"] == "Meeting"


class TestCalendarGetAvailableSlots:
    def test_returns_slots_when_mocked(self):
        with patch("rental_search_agent.server.do_calendar_get_available_slots") as m:
            m.return_value = [
                {"start": "2026-02-25T18:00:00", "end": "2026-02-25T19:00:00", "display": "Tue Feb 25, 06:00PM"},
            ]
            result = calendar_get_available_slots(
                "weekday evenings 6-8pm",
                "2026-02-25T00:00:00",
                "2026-03-05T00:00:00",
            )
            assert "slots" in result
            assert len(result["slots"]) == 1
            assert result["slots"][0]["display"] == "Tue Feb 25, 06:00PM"

    def test_auth_error_raises_value_error(self):
        with patch("rental_search_agent.server.do_calendar_get_available_slots") as m:
            m.side_effect = ValueError("credentials not found")
            with pytest.raises(ValueError, match="credentials not found"):
                calendar_get_available_slots(
                    "weekday evenings",
                    "2026-02-25T00:00:00",
                    "2026-03-05T00:00:00",
                )


class TestCalendarCreateEvent:
    def test_returns_event_when_mocked(self):
        with patch("rental_search_agent.server.do_calendar_create_event") as m:
            m.return_value = {"id": "ev123", "htmlLink": "https://calendar.google.com/ev123", "summary": "Viewing"}
            result = calendar_create_event(
                summary="Rental viewing: 123 Main St",
                start_datetime="2026-02-25T18:00:00",
                end_datetime="2026-02-25T19:00:00",
                listing_id="mls-001",
                listing_url="https://example.com/1",
            )
            assert result["id"] == "ev123"
            assert "Viewing" in result["summary"]


class TestCalendarUpdateEvent:
    def test_returns_event_when_mocked(self):
        with patch("rental_search_agent.server.do_calendar_update_event") as m:
            m.return_value = {"id": "ev123", "htmlLink": "https://calendar.google.com/ev123", "summary": "Updated"}
            result = calendar_update_event("ev123", start_datetime="2026-02-26T18:00:00", end_datetime="2026-02-26T19:00:00")
            assert result["id"] == "ev123"
            assert result["summary"] == "Updated"


class TestCalendarDeleteEvent:
    def test_returns_deleted_when_mocked(self):
        with patch("rental_search_agent.server.do_calendar_delete_event"):
            result = calendar_delete_event("ev123")
            assert result["deleted"] == "ev123"
