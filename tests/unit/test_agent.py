"""Unit tests for rental_search_agent.agent."""

import pytest

from rental_search_agent.agent import (
    build_approval_choices,
    flow_instructions,
    selected_to_listings,
)
from tests.fixtures.sample_data import sample_listing, sample_listings


class TestBuildApprovalChoices:
    def test_format_includes_id_suffix(self):
        listings = [sample_listing(id="mls-123", address="123 Main St", price=2800)]
        choices = build_approval_choices(listings)
        assert len(choices) == 1
        assert "[1]" in choices[0]
        assert "123 Main St" in choices[0]
        assert " (id: mls-123)" in choices[0]

    def test_multiple_listings(self):
        listings = sample_listings(3)
        choices = build_approval_choices(listings)
        assert len(choices) == 3
        assert "[1]" in choices[0]
        assert "[2]" in choices[1]
        assert "[3]" in choices[2]

    def test_empty_shortlist(self):
        assert build_approval_choices([]) == []


class TestSelectedToListings:
    def test_by_id_suffix(self):
        listings = [
            sample_listing(id="a"),
            sample_listing(id="b"),
        ]
        choices = build_approval_choices(listings)
        selected = [choices[0]]
        result = selected_to_listings(selected, listings)
        assert len(result) == 1
        assert result[0].id == "a"

    def test_by_raw_id(self):
        listings = [sample_listing(id="xyz")]
        result = selected_to_listings(["xyz"], listings)
        assert len(result) == 1
        assert result[0].id == "xyz"

    def test_empty_selected(self):
        listings = sample_listings(2)
        assert selected_to_listings([], listings) == []

    def test_unknown_id_skipped(self):
        listings = [sample_listing(id="a")]
        result = selected_to_listings(["unknown-id"], listings)
        assert result == []

    def test_multiple_selections(self):
        listings = [
            sample_listing(id="1"),
            sample_listing(id="2"),
            sample_listing(id="3"),
        ]
        choices = build_approval_choices(listings)
        selected = [choices[0], choices[2]]
        result = selected_to_listings(selected, listings)
        assert len(result) == 2
        assert result[0].id == "1"
        assert result[1].id == "3"

    def test_empty_string_skipped(self):
        listings = [sample_listing(id="a")]
        result = selected_to_listings(["", "a"], listings)
        assert len(result) == 1


class TestFlowInstructions:
    def test_returns_non_empty_string(self):
        instructions = flow_instructions()
        assert isinstance(instructions, str)
        assert len(instructions) > 100

    def test_contains_key_phrases(self):
        instructions = flow_instructions()
        assert "rental search assistant" in instructions.lower()
        assert "rental_search" in instructions or "rental search" in instructions
        assert "draft_viewing_plan" in instructions
        assert "calendar_get_available_slots" in instructions
