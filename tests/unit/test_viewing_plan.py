"""Unit tests for rental_search_agent.viewing_plan."""

import pytest

from rental_search_agent.viewing_plan import draft_viewing_plan, modify_viewing_plan
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


class TestModifyViewingPlan:
    def test_remove_one(self):
        plan = draft_viewing_plan(sample_listings_with_coords(), sample_available_slots(3))
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(3)
        result = modify_viewing_plan(entries, slots, remove=["mls-002"])
        assert len(result.entries) == 2
        ids = [e.listing_id for e in result.entries]
        assert "mls-001" in ids
        assert "mls-003" in ids
        assert "mls-002" not in ids

    def test_remove_many(self):
        plan = draft_viewing_plan(sample_listings_with_coords(), sample_available_slots(3))
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(3)
        result = modify_viewing_plan(entries, slots, remove=["mls-001", "mls-003"])
        assert len(result.entries) == 1
        assert result.entries[0].listing_id == "mls-002"

    def test_remove_all_returns_empty_plan(self):
        plan = draft_viewing_plan(
            [{"id": "a", "address": "A", "url": "https://a", "latitude": 49.28, "longitude": -123.12}],
            sample_available_slots(1),
        )
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(1)
        result = modify_viewing_plan(entries, slots, remove=["a"])
        assert len(result.entries) == 0

    def test_add_one(self):
        plan = draft_viewing_plan(
            [{"id": "a", "address": "A", "url": "https://a", "latitude": 49.28, "longitude": -123.12}],
            sample_available_slots(3),
        )
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(3)
        # Slot 0 used by "a"; slots 1 and 2 are unused
        unused = slots[1]
        result = modify_viewing_plan(
            entries,
            slots,
            add=[
                {
                    "listing_id": "b",
                    "listing_address": "B",
                    "listing_url": "https://b",
                    "slot": unused,
                }
            ],
        )
        assert len(result.entries) == 2
        ids = [e.listing_id for e in result.entries]
        assert "a" in ids
        assert "b" in ids
        b_entry = next(e for e in result.entries if e.listing_id == "b")
        assert b_entry.slot_display == unused["display"]
        assert b_entry.start_datetime == unused["start"]

    def test_update_slot(self):
        plan = draft_viewing_plan(
            [{"id": "a", "address": "A", "url": "https://a"}, {"id": "b", "address": "B", "url": "https://b"}],
            sample_available_slots(3),
        )
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(3)
        # Swap a to slot 2 (unused)
        new_slot = slots[2]
        result = modify_viewing_plan(
            entries,
            slots,
            update=[{"listing_id": "a", "new_slot": new_slot}],
        )
        a_entry = next(e for e in result.entries if e.listing_id == "a")
        assert a_entry.slot_display == new_slot["display"]
        assert a_entry.start_datetime == new_slot["start"]

    def test_update_slot_already_used_raises(self):
        plan = draft_viewing_plan(
            [{"id": "a", "address": "A", "url": "https://a"}, {"id": "b", "address": "B", "url": "https://b"}],
            sample_available_slots(2),
        )
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(2)
        # Try to move "a" to b's slot
        b_entry = next(e for e in plan.entries if e.listing_id == "b")
        with pytest.raises(ValueError, match="already used"):
            modify_viewing_plan(
                entries,
                slots,
                update=[
                    {
                        "listing_id": "a",
                        "new_slot": {"start": b_entry.start_datetime, "end": b_entry.end_datetime, "display": b_entry.slot_display},
                    }
                ],
            )

    def test_add_slot_not_in_available_raises(self):
        plan = draft_viewing_plan([{"id": "a", "address": "A", "url": "https://a"}], sample_available_slots(1))
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(1)
        with pytest.raises(ValueError, match="not in available_slots"):
            modify_viewing_plan(
                entries,
                slots,
                add=[
                    {
                        "listing_id": "b",
                        "listing_address": "B",
                        "listing_url": "https://b",
                        "slot": {"start": "2027-01-01T10:00:00", "end": "2027-01-01T11:00:00", "display": "Fake"},
                    }
                ],
            )

    def test_remove_listing_not_in_plan_raises(self):
        plan = draft_viewing_plan([{"id": "a", "address": "A", "url": "https://a"}], sample_available_slots(1))
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(1)
        with pytest.raises(ValueError, match="not found"):
            modify_viewing_plan(entries, slots, remove=["nonexistent"])

    def test_add_listing_already_in_plan_raises(self):
        plan = draft_viewing_plan(
            [{"id": "a", "address": "A", "url": "https://a"}, {"id": "b", "address": "B", "url": "https://b"}],
            sample_available_slots(3),
        )
        entries = [e.model_dump() for e in plan.entries]
        slots = sample_available_slots(3)
        unused = slots[2]
        with pytest.raises(ValueError, match="already in the plan"):
            modify_viewing_plan(
                entries,
                slots,
                add=[
                    {
                        "listing_id": "a",
                        "listing_address": "A",
                        "listing_url": "https://a",
                        "slot": unused,
                    }
                ],
            )
