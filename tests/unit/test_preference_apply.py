"""Unit tests for the deterministic preference apply pipeline."""

from unittest.mock import patch

from rental_search_agent.models import GeocodedReference, ProximityRule
from rental_search_agent.preference_apply import (
    APPLY_TOOL_NAME,
    apply_search_preferences,
    overlay_structural_on_search_filters,
    pipeline_needed,
    prepare_sidebar_search,
    scrape_prefs_changed,
    structural_prefs_changed,
    with_display_rank,
)
from rental_search_agent.preference_resolution import (
    EffectiveSearchPreferences,
    fill_empty_stored_from_chat,
    stored_prefs_to_effective,
)
from tests.fixtures.sample_data import sample_listing


def _listing_dict(**kwargs) -> dict:
    return sample_listing(**kwargs).model_dump()


class TestStructuralPrefsChanged:
    def test_no_change(self):
        prefs = {"budget_max": "2800", "min_bedrooms": "2", "proximity_preferences": "x"}
        assert structural_prefs_changed(prefs, dict(prefs)) is False

    def test_budget_change_is_structural(self):
        before = {"budget_max": "2800", "min_bedrooms": "2"}
        after = {"budget_max": "2500", "min_bedrooms": "2"}
        assert structural_prefs_changed(before, after) is True

    def test_proximity_only_is_not_structural(self):
        before = {"budget_max": "2800", "proximity_preferences": "30 min downtown"}
        after = {"budget_max": "2800", "proximity_preferences": "15 min downtown"}
        assert structural_prefs_changed(before, after) is False

    def test_qualitative_only_is_not_structural(self):
        before = {"min_bedrooms": "2", "qualitative_preferences": "balcony"}
        after = {"min_bedrooms": "2", "qualitative_preferences": "parking"}
        assert structural_prefs_changed(before, after) is False

    def test_require_den_only_is_not_structural(self):
        before = {"min_bedrooms": "2", "require_den": ""}
        after = {"min_bedrooms": "2", "require_den": "true"}
        assert structural_prefs_changed(before, after) is False

    def test_beds_change_is_structural(self):
        before = {"min_bedrooms": "2"}
        after = {"min_bedrooms": "3"}
        assert structural_prefs_changed(before, after) is True

    def test_location_change_is_scrape(self):
        before = {"location": "Vancouver", "min_bedrooms": "2"}
        after = {"location": "Metro Vancouver", "min_bedrooms": "2"}
        assert scrape_prefs_changed(before, after) is True
        assert structural_prefs_changed(before, after) is True

    def test_listing_type_change_is_scrape(self):
        before = {"listing_type": "for_rent", "min_bedrooms": "2"}
        after = {"listing_type": "for_sale", "min_bedrooms": "2"}
        assert scrape_prefs_changed(before, after) is True

    def test_proximity_and_den_are_not_scrape(self):
        before = {
            "location": "Vancouver",
            "listing_type": "for_rent",
            "min_bedrooms": "2",
            "proximity_preferences": "30 min",
            "require_den": "",
        }
        after = dict(before)
        after["proximity_preferences"] = "15 min"
        after["require_den"] = "true"
        after["qualitative_preferences"] = "balcony"
        assert scrape_prefs_changed(before, after) is False


class TestOverlayStructuralOnSearchFilters:
    def test_keeps_location_and_overlays_budget(self):
        last = {"min_bedrooms": 2, "location": "Vancouver, BC", "listing_type": "for_rent"}
        prefs = {"budget_max": "2500", "min_bedrooms": "2"}
        out = overlay_structural_on_search_filters(last, prefs)
        assert out["location"] == "Vancouver, BC"
        assert out["price_max"] == 2500.0
        assert out["min_bedrooms"] == 2
        assert out["listing_type"] == "for_rent"

    def test_cleared_budget_drops_price_max(self):
        last = {"min_bedrooms": 2, "location": "Vancouver", "price_max": 3000}
        prefs = {"budget_max": "", "min_bedrooms": "2"}
        out = overlay_structural_on_search_filters(last, prefs)
        assert "price_max" not in out
        assert out["min_bedrooms"] == 2

    def test_preserves_last_search_bounds_the_form_does_not_own(self):
        last = {
            "min_bedrooms": 2,
            "max_bedrooms": 2,
            "location": "Vancouver",
            "price_min": 1000,
            "max_sqft": 1200,
            "max_bathrooms": 2,
            "price_max": 3000,
        }
        previous = {"budget_max": "3000", "min_bedrooms": "2"}
        prefs = {"budget_max": "2500", "min_bedrooms": "2"}
        out = overlay_structural_on_search_filters(
            last, prefs, previous_prefs=previous
        )
        assert out["max_bedrooms"] == 2
        assert out["price_min"] == 1000
        assert out["max_sqft"] == 1200
        assert out["max_bathrooms"] == 2
        assert out["price_max"] == 2500.0
        assert out["min_bedrooms"] == 2

    def test_budget_only_keeps_last_location_list(self):
        last = {
            "min_bedrooms": 2,
            "location": ["Vancouver, BC", "Burnaby, BC"],
            "listing_type": "for_rent",
            "price_max": 3000,
        }
        previous = {
            "location": "Metro Vancouver",
            "listing_type": "for_rent",
            "budget_max": "3000",
            "min_bedrooms": "2",
        }
        prefs = dict(previous)
        prefs["budget_max"] = "2500"
        out = overlay_structural_on_search_filters(last, prefs, previous_prefs=previous)
        assert out["location"] == ["Vancouver, BC", "Burnaby, BC"]
        assert out["price_max"] == 2500.0
        assert out["listing_type"] == "for_rent"

    def test_location_change_uses_expanded_metro_list(self):
        last = {
            "min_bedrooms": 2,
            "location": "Vancouver, BC",
            "listing_type": "for_rent",
        }
        previous = {"location": "Vancouver", "min_bedrooms": "2", "listing_type": "for_rent"}
        prefs = {"location": "Metro Vancouver", "min_bedrooms": "2", "listing_type": "for_rent"}
        out = overlay_structural_on_search_filters(last, prefs, previous_prefs=previous)
        assert isinstance(out["location"], list)
        assert "Vancouver, BC" in out["location"]
        assert "Burnaby, BC" in out["location"]
        assert len(out["location"]) > 1


class TestPrepareSidebarSearch:
    def test_first_search_scrapes_when_location_and_beds_set(self):
        prefs = {
            "location": "Vancouver",
            "min_bedrooms": "2",
            "listing_type": "for_rent",
        }
        req = prepare_sidebar_search(prefs, {}, has_master=False, last_filters=None)
        assert req.kind == "scrape"
        assert req.filters is not None
        assert req.filters["location"] == "Vancouver"
        assert req.filters["min_bedrooms"] == 2
        assert req.filters["listing_type"] == "for_rent"

    def test_first_search_missing_location_errors(self):
        prefs = {"location": "", "min_bedrooms": "2"}
        req = prepare_sidebar_search(prefs, {}, has_master=False, last_filters=None)
        assert req.kind == "error"
        assert any("location" in w.lower() for w in req.warnings)

    def test_first_search_missing_beds_errors(self):
        prefs = {"location": "Vancouver", "min_bedrooms": ""}
        req = prepare_sidebar_search(prefs, {}, has_master=False, last_filters=None)
        assert req.kind == "error"
        assert any("bedroom" in w.lower() for w in req.warnings)

    def test_soft_keys_only_reranks_when_master_exists(self):
        previous = {
            "location": "Vancouver",
            "listing_type": "for_rent",
            "min_bedrooms": "2",
            "proximity_preferences": "30 min downtown",
        }
        prefs = dict(previous)
        prefs["proximity_preferences"] = "15 min downtown"
        req = prepare_sidebar_search(
            prefs,
            previous,
            has_master=True,
            last_filters={"location": "Vancouver, BC", "min_bedrooms": 2},
        )
        assert req.kind == "rerank"

    def test_hard_key_change_scrapes_with_overlay(self):
        previous = {
            "location": "Vancouver",
            "listing_type": "for_rent",
            "min_bedrooms": "2",
            "budget_max": "3000",
        }
        prefs = dict(previous)
        prefs["budget_max"] = "2500"
        last = {
            "location": "Vancouver, BC",
            "min_bedrooms": 2,
            "listing_type": "for_rent",
            "price_max": 3000,
        }
        req = prepare_sidebar_search(
            prefs, previous, has_master=True, last_filters=last
        )
        assert req.kind == "scrape"
        assert req.filters is not None
        assert req.filters["location"] == "Vancouver, BC"
        assert req.filters["price_max"] == 2500.0


class TestStoredLocationAndListingType:
    def test_stored_prefs_include_location_and_listing_type(self):
        effective = stored_prefs_to_effective(
            {"location": "Metro Vancouver", "listing_type": "for_sale", "min_bedrooms": "2"}
        )
        assert effective.location == "Metro Vancouver"
        assert effective.listing_type == "for_sale"
        assert effective.min_bedrooms == 2

    def test_fill_empty_from_chat_does_not_overwrite(self):
        stored = {
            "location": "Vancouver",
            "listing_type": "",
            "budget_max": "",
            "min_bedrooms": "",
        }
        filled = fill_empty_stored_from_chat(
            stored,
            {"location": "Toronto", "listing_type": "for_sale", "min_bedrooms": 2},
        )
        assert filled["location"] == "Vancouver"
        assert filled["listing_type"] == "for_sale"
        assert filled["min_bedrooms"] == "2"


class TestStructuralPrefsForRerank:
    def test_stored_budget_applies_when_missing_from_search_args(self):
        from rental_search_agent.preference_apply import structural_prefs_for_rerank

        prefs = {"budget_max": "2800", "min_bedrooms": "2"}
        criteria = {"min_bedrooms": 2, "location": "Vancouver"}
        effective = structural_prefs_for_rerank(prefs, criteria)
        assert effective.budget_max == 2800.0
        assert effective.min_bedrooms == 2

    def test_chat_search_criteria_override_stored(self):
        from rental_search_agent.preference_apply import structural_prefs_for_rerank

        prefs = {"budget_max": "2800", "min_bedrooms": "2"}
        criteria = {"min_bedrooms": 2, "price_max": 2500}
        effective = structural_prefs_for_rerank(prefs, criteria)
        assert effective.budget_max == 2500.0


class TestPipelineNeeded:
    def test_empty_prefs_not_needed(self):
        assert pipeline_needed(EffectiveSearchPreferences()) is False

    def test_beds_needed(self):
        assert pipeline_needed(EffectiveSearchPreferences(min_bedrooms=2)) is True

    def test_proximity_needed(self):
        assert pipeline_needed(
            EffectiveSearchPreferences(proximity_preferences="30 min drive to downtown")
        ) is True


class TestApplySearchPreferences:
    def test_structural_only_filters_and_scores(self):
        listings = [
            _listing_dict(id="cheap", price=2000.0, bedrooms=2),
            _listing_dict(id="steep", price=4000.0, bedrooms=2),
            _listing_dict(id="studio", price=1800.0, bedrooms=1),
        ]
        prefs = EffectiveSearchPreferences(min_bedrooms=2, budget_max=2800.0)
        result = apply_search_preferences(listings, prefs)
        assert result.applied is True
        ids = [lst["id"] for lst in result.listings]
        assert "studio" not in ids
        assert "steep" not in ids
        assert ids == ["cheap"]
        assert result.listings[0]["match_score"] is not None
        assert result.last_sort_by == "match_score"
        assert result.display_source == "score"
        assert result.listings[0]["rank"] == 1

    def test_empty_prefs_is_noop(self):
        listings = [_listing_dict(id="a")]
        result = apply_search_preferences(listings, EffectiveSearchPreferences())
        assert result.applied is False
        assert [lst["id"] for lst in result.listings] == ["a"]
        assert "structural" in result.skipped
        assert "proximity" in result.skipped
        assert "score" in result.skipped

    def test_den_does_not_drop_listings(self):
        listings = [
            _listing_dict(id="no-den", bedrooms=2, has_den=False),
            _listing_dict(id="has-den", bedrooms=2, has_den=True),
        ]
        prefs = EffectiveSearchPreferences(min_bedrooms=2, require_den=True)
        result = apply_search_preferences(listings, prefs)
        ids = {lst["id"] for lst in result.listings}
        assert ids == {"no-den", "has-den"}

    def test_proximity_then_score(self):
        listings = [
            _listing_dict(id="near", bedrooms=2, latitude=49.28, longitude=-123.12),
            _listing_dict(id="far", bedrooms=2, latitude=49.18, longitude=-122.85),
        ]
        rule = ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=20)
        refs = [
            GeocodedReference(location="Downtown Vancouver", lat=49.28, lon=-123.12)
        ]
        enriched = []
        for lst in listings:
            d = dict(lst)
            duration = 10.0 if d["id"] == "near" else 45.0
            d["proximity"] = {
                "Downtown Vancouver|drive": {"distance_km": 1.0, "duration_min": duration}
            }
            enriched.append(d)
        prefs = EffectiveSearchPreferences(
            min_bedrooms=2,
            proximity_preferences="max 20 min drive to Downtown Vancouver",
        )
        with (
            patch(
                "rental_search_agent.preference_apply.parse_proximity_preferences",
                return_value=[rule],
            ),
            patch(
                "rental_search_agent.preference_apply.geocode_proximity_references",
                return_value=refs,
            ),
            patch(
                "rental_search_agent.preference_apply.enrich_listings_with_proximity",
                return_value=enriched,
            ),
        ):
            result = apply_search_preferences(listings, prefs)
        ids = [lst["id"] for lst in result.listings]
        assert ids == ["near"]
        assert result.proximity_rules
        assert result.last_sort_by == "match_score"
        assert result.display_source == "score"

    def test_geocode_failure_keeps_listings_and_warns(self):
        listings = [_listing_dict(id="a", bedrooms=2)]
        prefs = EffectiveSearchPreferences(
            proximity_preferences="max 20 min drive to downtown"
        )
        with (
            patch(
                "rental_search_agent.preference_apply.parse_proximity_preferences",
                return_value=[
                    ProximityRule(location="downtown", mode="drive", max_minutes=20)
                ],
            ),
            patch(
                "rental_search_agent.preference_apply.geocode_proximity_references",
                side_effect=ValueError("GOOGLE_MAPS_API_KEY is not set"),
            ),
        ):
            result = apply_search_preferences(listings, prefs)
        assert [lst["id"] for lst in result.listings] == ["a"]
        assert "proximity" in result.skipped
        assert result.warnings
        assert any("GOOGLE_MAPS_API_KEY" in w for w in result.warnings)

    def test_structural_prefs_override_for_filter_only(self):
        listings = [
            _listing_dict(id="one", bedrooms=1, price=2000.0),
            _listing_dict(id="two", bedrooms=2, price=2000.0),
        ]
        score_prefs = EffectiveSearchPreferences(
            qualitative_preferences="balcony",
            min_bedrooms=1,
        )
        structural = EffectiveSearchPreferences(min_bedrooms=2)
        with patch(
            "rental_search_agent.preference_apply.score_listings_by_preferences",
            side_effect=lambda items, **kwargs: [dict(x) for x in items],
        ):
            result = apply_search_preferences(
                listings, score_prefs, structural_prefs=structural
            )
        ids = [lst["id"] for lst in result.listings]
        assert ids == ["two"]

    def test_with_display_rank_is_1_based(self):
        ranked = with_display_rank([{"id": "a"}, {"id": "b"}])
        assert ranked[0]["rank"] == 1
        assert ranked[1]["rank"] == 2


class TestMakeApplyToolMessages:
    def test_payload_round_trips(self):
        from rental_search_agent.preference_apply import (
            ApplyPreferencesResult,
            make_apply_tool_messages,
        )

        result = ApplyPreferencesResult(
            listings=[{"id": "a", "rank": 1}],
            last_sort_by="match_score",
            display_source="score",
            applied=True,
        )
        msgs = make_apply_tool_messages(result)
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"][0]["function"]["name"] == APPLY_TOOL_NAME
        assert msgs[1]["role"] == "tool"
