"""Unit tests for preferred facing parse, extract, circular OR score, and merge."""

from unittest.mock import patch

from rental_search_agent.match_scoring import combine_component_scores, score_listings_by_preferences
from rental_search_agent.preference_apply import scrape_prefs_changed
from rental_search_agent.preference_criteria import qualitative_for_semantic
from rental_search_agent.preference_resolution import (
    PREF_KEYS,
    EffectiveSearchPreferences,
    fill_empty_stored_from_chat,
    merge_chat_over_stored,
    qualitative_from_preferences_text,
    stored_prefs_to_effective,
)
from rental_search_agent.preferred_directions import (
    STEP_SCORES,
    best_direction_score,
    best_matching_observed,
    chat_direction_override,
    evaluate_direction_criterion,
    extract_listing_facings,
    pairwise_score,
    parse_preferred_directions,
    preferred_directions_from_preferences_text,
    score_direction,
    serialize_preferred_directions,
)


def _listing(**kwargs) -> dict:
    base = {
        "id": "1",
        "title": "Test",
        "url": "https://example.com/1",
        "address": "123 Main St",
        "price": 2500.0,
        "bedrooms": 2,
        "description": "",
        "ammenities": "",
        "title": "Condo",
    }
    base.update(kwargs)
    return base


class TestParseAndSerialize:
    def test_csv_and_labels(self):
        assert parse_preferred_directions("S,SW,W") == ["S", "SW", "W"]
        assert parse_preferred_directions("South, West") == ["S", "W"]
        assert parse_preferred_directions(["North-East", "S"]) == ["NE", "S"]
        assert serialize_preferred_directions(["W", "S", "SW"]) == "S,SW,W"

    def test_empty(self):
        assert parse_preferred_directions("") == []
        assert parse_preferred_directions(None) == []
        assert serialize_preferred_directions([]) == ""


class TestCircularAdjacency:
    def test_sw_pref_s_higher_than_n_and_w_higher_than_e(self):
        assert pairwise_score("S", "SW") == STEP_SCORES[1]
        assert pairwise_score("W", "SW") == STEP_SCORES[1]
        assert pairwise_score("N", "SW") == STEP_SCORES[3]
        assert pairwise_score("E", "SW") == STEP_SCORES[3]
        assert pairwise_score("S", "SW") > pairwise_score("N", "SW")
        assert pairwise_score("W", "SW") > pairwise_score("E", "SW")

    def test_exact_and_opposite(self):
        assert pairwise_score("SW", "SW") == 1.0
        assert pairwise_score("NE", "SW") == 0.0


class TestOrAggregation:
    def test_selected_s_and_w_listing_sw_uses_best_adjacent(self):
        score = best_direction_score(["SW"], ["S", "W"])
        assert score == STEP_SCORES[1]

    def test_corner_unit_uses_max(self):
        # South+west observed vs SW pref: SW is exact on the pair's closer point... 
        # observed S and W vs preferred SW → max(S-SW, W-SW) = 0.70
        assert best_direction_score(["S", "W"], ["SW"]) == STEP_SCORES[1]
        # observed SW vs preferred S,W → same
        assert best_direction_score(["SW"], ["S", "W"]) == STEP_SCORES[1]
        # exact among two observed
        assert best_direction_score(["S", "W"], ["S"]) == 1.0


class TestChecklistShowsMatchedFacingOnly:
    def test_met_row_shows_listing_match_not_all_prefs(self):
        listing = _listing(description="Bright south-facing balcony with city views.")
        row = evaluate_direction_criterion(listing, ["E", "SE", "S", "SW"])
        assert row.status == "met"
        assert row.observed == "South"
        assert row.required is None
        assert row.comparator is None
        from rental_search_agent.analysis_view import checklist_item_to_row

        view_row = checklist_item_to_row(
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "group": row.group,
                "observed": row.observed,
                "required": row.required,
                "comparator": row.comparator,
                "source": row.source,
            }
        )
        assert view_row.comparison_text == "South"
        assert "East" not in view_row.comparison_text
        assert "OR" not in view_row.comparison_text

    def test_best_matching_observed_picks_exact_over_adjacent(self):
        assert best_matching_observed(["S", "E"], ["S", "SW"]) == ["S"]

    def test_partial_row_shows_listing_facing(self):
        listing = _listing(description="South-facing living room.")
        row = evaluate_direction_criterion(listing, ["SW"])
        assert row.status == "partial"
        assert row.observed == "South"


class TestListingExtract:
    def test_south_facing_balcony(self):
        listing = _listing(description="Bright south-facing balcony with city views.")
        assert extract_listing_facings(listing) == ["S"]

    def test_southwest_exposure(self):
        listing = _listing(description="Corner unit with SW exposure and mountain views.")
        assert extract_listing_facings(listing) == ["SW"]

    def test_south_and_west_facing(self):
        listing = _listing(description="South and west facing windows throughout.")
        assert extract_listing_facings(listing) == ["S", "W"]

    def test_north_vancouver_is_not_facing(self):
        listing = _listing(description="Beautiful condo in North Vancouver near Lonsdale.")
        assert extract_listing_facings(listing) == []

    def test_south_of_fraser_is_not_facing(self):
        listing = _listing(description="Conveniently located south of Fraser with shops nearby.")
        assert extract_listing_facings(listing) == []

    def test_east_side_is_not_facing(self):
        listing = _listing(description="Located on the east side close to Commercial Drive.")
        assert extract_listing_facings(listing) == []


class TestScoreOmitUnknown:
    def test_no_mention_omits_component(self):
        listing = _listing(description="Two bedroom condo with balcony and parking.")
        assert score_direction(listing, ["S"]) is None

    def test_opposite_mention_is_included_and_low(self):
        listing = _listing(description="Northeast-facing living room.")
        score = score_direction(listing, ["SW"])
        assert score == 0.0

    def test_no_prefs_omits(self):
        listing = _listing(description="South-facing balcony.")
        assert score_direction(listing, []) is None

    def test_match_score_includes_direction_when_known(self):
        listings = [_listing(description="South-facing balcony and parking.")]
        prefs = EffectiveSearchPreferences(preferred_directions=["S"])
        with patch("rental_search_agent.match_scoring.embed_texts", side_effect=RuntimeError("skip")):
            scored = score_listings_by_preferences(listings, effective_prefs=prefs)
        assert "direction" in scored[0]["score_breakdown"]["included"]
        assert scored[0]["score_breakdown"]["components"]["direction"] == 1.0

    def test_match_score_omits_direction_when_unknown(self):
        listings = [_listing(description="Two bedroom condo with balcony.")]
        prefs = EffectiveSearchPreferences(preferred_directions=["S"])
        with patch("rental_search_agent.match_scoring.embed_texts", side_effect=RuntimeError("skip")):
            scored = score_listings_by_preferences(listings, effective_prefs=prefs)
        assert "direction" not in scored[0]["score_breakdown"]["included"]
        assert scored[0]["score_breakdown"]["components"]["direction"] is None


class TestPreferencesTextParse:
    def test_facing_block_overrides(self):
        text = "balcony, parking\n\nFacing: South, West"
        assert preferred_directions_from_preferences_text(text) == ["S", "W"]
        assert qualitative_from_preferences_text(text) == "balcony, parking"

    def test_south_facing_phrase(self):
        assert preferred_directions_from_preferences_text("south-facing please") == ["S"]

    def test_amenities_text_is_not_directions(self):
        assert preferred_directions_from_preferences_text("balcony, parking, gym") == []

    def test_explicit_arg_wins_over_text(self):
        assert chat_direction_override("Facing: South", ["W"]) == ["W"]


class TestChatMergeAndFillEmpty:
    def test_chat_list_replaces_stored(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["preferred_directions"] = "SW"
        effective = merge_chat_over_stored(stored, {"preferred_directions": ["S", "W"]})
        assert effective.preferred_directions == ["S", "W"]

    def test_fill_empty_persists_when_stored_blank(self):
        stored = {k: "" for k in PREF_KEYS}
        filled = fill_empty_stored_from_chat(stored, {"preferred_directions": ["S"]})
        assert filled["preferred_directions"] == "S"

    def test_fill_empty_does_not_overwrite(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["preferred_directions"] = "SW"
        filled = fill_empty_stored_from_chat(stored, {"preferred_directions": ["S"]})
        assert filled["preferred_directions"] == "SW"

    def test_stored_csv_to_effective(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["preferred_directions"] = "S,W"
        effective = stored_prefs_to_effective(stored)
        assert effective.preferred_directions == ["S", "W"]
        assert effective.has_score_relevant_prefs()

    def test_qualitative_does_not_become_directions(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["qualitative_preferences"] = "south-facing balcony, parking"
        effective = stored_prefs_to_effective(stored)
        assert effective.preferred_directions == []
        assert "south-facing" in effective.qualitative_preferences


class TestScrapePrefsUnchanged:
    def test_direction_only_is_not_scrape(self):
        before = {
            "location": "Vancouver",
            "listing_type": "for_rent",
            "min_bedrooms": "2",
            "preferred_directions": "",
        }
        after = dict(before)
        after["preferred_directions"] = "S,W"
        assert scrape_prefs_changed(before, after) is False


class TestSemanticPhrase:
    def test_appends_facing_phrase_not_from_qualitative_scan(self):
        prefs = EffectiveSearchPreferences(
            qualitative_preferences="balcony",
            preferred_directions=["S", "W"],
        )
        blob = qualitative_for_semantic(prefs)
        assert "balcony" in blob
        assert "south-facing" in blob
        assert "west-facing" in blob


class TestCombineIncludesDirection:
    def test_direction_participates_when_present(self):
        weights = {
            "structural": 0.5,
            "proximity": 0.0,
            "amenity": 0.0,
            "direction": 0.5,
            "semantic": 0.0,
        }
        result = combine_component_scores(
            {
                "structural": 1.0,
                "proximity": None,
                "amenity": None,
                "direction": 0.0,
                "semantic": None,
            },
            weights,
        )
        assert result == 0.5
