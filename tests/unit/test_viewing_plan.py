"""Unit tests for rental_search_agent.viewing_plan."""

import pytest

from rental_search_agent.viewing_plan import draft_viewing_plan
from tests.fixtures.sample_data import sample_available_slots, sample_listings_with_coords


class TestDraftViewingPlan:
    def test_empty_listings_returns_empty_plan(self):
        slots = sample_available_slots(2)
        result = draft_viewing_plan([], slots)
        assert result.entries == []

    def test_clusters_nearby_listings_adjacent_slots(self):
        listings = sample_listings_with_coords()
        slots = sample_available_slots(3)
        result = draft_viewing_plan(listings, slots)
        assert len(result.entries) == 3
        # Downtown Vancouver (mls-001, mls-002) should be in one cluster, Surrey (mls-003) in another
        # Order: cluster by centroid; downtown first (higher lat), then Surrey
        ids = [e.listing_id for e in result.entries]
        assert "mls-001" in ids
        assert "mls-002" in ids
        assert "mls-003" in ids
        # First two slots go to downtown cluster, third to Surrey
        assert result.entries[0].slot_display == slots[0]["display"]
        assert result.entries[1].slot_display == slots[1]["display"]
        assert result.entries[2].slot_display == slots[2]["display"]

    def test_listings_without_coords_appended_last(self):
        listings = [
            {"id": "a", "address": "A", "url": "https://a", "latitude": 49.28, "longitude": -123.12},
            {"id": "b", "address": "B", "url": "https://b"},
        ]
        slots = sample_available_slots(2)
        result = draft_viewing_plan(listings, slots)
        assert len(result.entries) == 2
        assert result.entries[0].listing_id == "a"
        assert result.entries[1].listing_id == "b"

    def test_more_listings_than_slots_raises(self):
        listings = sample_listings_with_coords()
        slots = sample_available_slots(2)
        with pytest.raises(ValueError, match="Not enough slots"):
            draft_viewing_plan(listings, slots)

    def test_empty_slots_raises(self):
        listings = [{"id": "1", "address": "A", "url": "https://a"}]
        with pytest.raises(ValueError, match="No available slots"):
            draft_viewing_plan(listings, [])

    def test_more_slots_than_listings_uses_only_needed(self):
        listings = [{"id": "1", "address": "A", "url": "https://a", "latitude": 49.28, "longitude": -123.12}]
        slots = sample_available_slots(5)
        result = draft_viewing_plan(listings, slots)
        assert len(result.entries) == 1
        assert result.entries[0].listing_id == "1"
        assert result.entries[0].slot_display == slots[0]["display"]
        assert result.entries[0].start_datetime == slots[0]["start"]
        assert result.entries[0].end_datetime == slots[0]["end"]
