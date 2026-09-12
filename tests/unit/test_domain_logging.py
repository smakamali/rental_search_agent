"""Unit tests for Phase 2 domain-decision logging (caplog)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from rental_search_agent.filtering import filter_listings
from rental_search_agent.match_scoring import score_listings_by_preferences
from rental_search_agent.models import ListingFilterCriteria, ProximityRule
from rental_search_agent.preference_resolution import (
    EffectiveSearchPreferences,
    load_stored_preferences,
    merge_chat_over_stored,
    stored_prefs_to_effective,
)
from rental_search_agent.search_regions import (
    canonicalize_search_locations,
    expand_search_region,
    resolve_search_location_input,
)
from tests.fixtures.sample_data import sample_listing


class TestSearchRegionsLogging:
    def test_expand_unknown_region_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.search_regions"):
            result = expand_search_region("the Prairies")
        assert "error" in result
        assert any(
            r.levelno == logging.WARNING and "unknown region" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_expand_blank_region_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.search_regions"):
            result = expand_search_region("  ")
        assert "error" in result
        assert any("blank region" in r.getMessage() for r in caplog.records)

    def test_expand_known_region_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="rental_search_agent.search_regions"):
            result = expand_search_region("GTA")
        assert result["region"] == "Greater Toronto Area"
        assert any(
            r.levelno == logging.DEBUG
            and "expand_search_region" in r.getMessage()
            and "n_cities=" in r.getMessage()
            for r in caplog.records
        )

    def test_canonicalize_debug_reports_dupes(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="rental_search_agent.search_regions"):
            locs = canonicalize_search_locations(
                ["City of North Vancouver", "District of North Vancouver"]
            )
        assert locs == ["North Vancouver, BC"]
        assert any(
            "canonicalize_search_locations" in r.getMessage() and "n_dup_dropped=1" in r.getMessage()
            for r in caplog.records
        )

    def test_resolve_metro_logs_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="rental_search_agent.search_regions"):
            resolved = resolve_search_location_input("Metro Vancouver")
        assert isinstance(resolved, list)
        assert any(
            "resolve_search_location_input" in r.getMessage() and "n_locations=" in r.getMessage()
            for r in caplog.records
        )


class TestFilteringLogging:
    def test_wipe_warns_when_all_dropped(self, caplog):
        listings = [sample_listing(bedrooms=1), sample_listing(id="mls-002", bedrooms=2)]
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.filtering"):
            result = filter_listings(listings, ListingFilterCriteria(min_bedrooms=5))
        assert result.total_count == 0
        assert any(
            r.levelno == logging.WARNING
            and "wiped all listings" in r.getMessage()
            and "drops=" in r.getMessage()
            for r in caplog.records
        )

    def test_debug_histogram_for_partial_filter(self, caplog):
        listings = [
            sample_listing(bedrooms=1, price=2000),
            sample_listing(id="mls-002", bedrooms=3, price=4000),
        ]
        with caplog.at_level(logging.DEBUG, logger="rental_search_agent.filtering"):
            result = filter_listings(
                listings,
                ListingFilterCriteria(min_bedrooms=2, price_max=3000),
            )
        assert result.total_count == 0
        msgs = [r.getMessage() for r in caplog.records if "filter_listings" in r.getMessage()]
        assert msgs
        assert any("drops=" in m for m in msgs)

    def test_proximity_drop_counted(self, caplog):
        listings = [
            sample_listing(
                proximity={"downtown|drive": {"duration_min": 60}},
            )
        ]
        rules = [ProximityRule(location="downtown", mode="drive", max_minutes=20)]
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.filtering"):
            result = filter_listings(listings, ListingFilterCriteria(), proximity_rules=rules)
        assert result.total_count == 0
        assert any("proximity" in r.getMessage() for r in caplog.records)


class TestPreferenceResolutionLogging:
    def test_parse_failure_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.preference_resolution"):
            prefs = stored_prefs_to_effective({"budget_max": "not-a-number", "require_den": "maybe"})
        assert prefs.budget_max is None
        assert prefs.require_den is None
        messages = [r.getMessage() for r in caplog.records]
        assert any("budget_max" in m and "float" in m for m in messages)
        assert any("require_den" in m and "bool" in m for m in messages)

    def test_empty_parse_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING, logger="rental_search_agent.preference_resolution"):
            stored_prefs_to_effective({"budget_max": "  ", "min_bedrooms": ""})
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_load_corrupt_prefs_warns(self, caplog, tmp_path: Path):
        bad = tmp_path / "preferences.json"
        bad.write_text("{not-json", encoding="utf-8")
        with patch(
            "rental_search_agent.preference_resolution.preferences_file_path",
            return_value=bad,
        ):
            with caplog.at_level(logging.WARNING, logger="rental_search_agent.preference_resolution"):
                prefs = load_stored_preferences()
        assert prefs["location"] == ""
        assert any(
            "load_stored_preferences failed" in r.getMessage()
            or "Failed to load preferences" in r.getMessage()
            for r in caplog.records
        )

    def test_merge_debug_effective_keys(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="rental_search_agent.preference_resolution"):
            merge_chat_over_stored(
                {"budget_max": "2500"},
                {"min_bedrooms": 2},
            )
        assert any(
            "merge_chat_over_stored" in r.getMessage() and "effective_keys=" in r.getMessage()
            for r in caplog.records
        )


class TestMatchScoringLogging:
    def test_score_stage_start_end(self, caplog):
        listings = [sample_listing().model_dump()]
        prefs = EffectiveSearchPreferences(budget_max=3000, min_bedrooms=2)
        # DEBUG: apply_search_preferences owns the INFO apply boundary.
        with caplog.at_level(logging.DEBUG, logger="rental_search_agent.match_scoring"):
            scored = score_listings_by_preferences(listings, effective_prefs=prefs)
        assert len(scored) == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("stage start name=score_listings_by_preferences" in m for m in messages)
        assert any("stage end name=score_listings_by_preferences" in m for m in messages)
        assert not any(
            r.levelno == logging.INFO and "score_listings_by_preferences" in r.getMessage()
            for r in caplog.records
        )

    def test_embed_failure_warn_includes_n_texts(self, caplog):
        listings = [
            {
                **sample_listing().model_dump(),
                "description": "Bright unit with balcony",
            }
        ]
        prefs = EffectiveSearchPreferences(qualitative_preferences="balcony parking")
        with patch(
            "rental_search_agent.match_scoring.embed_texts",
            side_effect=RuntimeError("embed down"),
        ):
            with caplog.at_level(logging.WARNING, logger="rental_search_agent.match_scoring"):
                score_listings_by_preferences(listings, effective_prefs=prefs)
        assert any(
            "n_texts=" in r.getMessage() and "Semantic component embedding failed" in r.getMessage()
            for r in caplog.records
        )
        assert any(
            r.exc_info is not None and "Semantic component embedding failed" in r.getMessage()
            for r in caplog.records
        )
