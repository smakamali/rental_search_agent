"""Unit tests for search-results presentation helpers."""

from rental_search_agent.display_format import get_score_color
from rental_search_agent.streamlit_results import (
    _format_match_score,
    _format_proximity_display,
    _listings_to_table_rows,
    compact_match_score_html,
    format_property_basics,
    format_proximity_caption,
    format_sort_by_label,
    listing_address_parts,
    listing_tag_labels,
    ordered_by_caption,
    proximity_card_lines,
    results_count_label,
    listing_match_score,
    match_score_display,
    normalize_map_label_mode,
    normalize_results_view,
    parse_proximity_display,
    prepare_map_label_widget_state,
    prepare_results_widget_state,
    proximity_unavailable_summary,
)


class TestProximityDisplay:
    def test_parses_available_duration_row(self):
        items = parse_proximity_display(
            {"800 Burrard St|drive": {"duration_min": 23, "distance_km": 8.4}}
        )
        assert len(items) == 1
        assert items[0].status == "available"
        assert items[0].location == "800 Burrard St"
        assert items[0].mode == "drive"
        assert items[0].duration_min == 23
        assert items[0].display_text == "23 min to 800 Burrard St"
        assert items[0].unavailable_text is None

    def test_nearest_transit_uses_natural_wording(self):
        items = parse_proximity_display(
            {"nearest transit station|walk": {"duration_min": 2, "distance_km": 0.2}}
        )
        assert items[0].status == "available"
        assert items[0].display_text == "2 min walk to transit"

    def test_named_unavailable_criterion(self):
        items = parse_proximity_display({"Metrotown|drive": None})
        assert len(items) == 1
        assert items[0].status == "unavailable"
        assert items[0].unavailable_text == "Drive to Metrotown unavailable"
        assert items[0].display_text == "Drive to Metrotown unavailable"

    def test_unavailable_count_singular_and_plural(self):
        one = parse_proximity_display({"Metrotown|drive": None})
        two = parse_proximity_display(
            {
                "Metrotown|drive": None,
                "downtown|walk": {},
            }
        )
        assert proximity_unavailable_summary(one) == "1 proximity criterion unavailable"
        assert proximity_unavailable_summary(two) == "2 proximity criteria unavailable"

    def test_caption_names_single_unavailable_and_counts_multiple(self):
        mixed = parse_proximity_display(
            {
                "800 Burrard St|drive": {"duration_min": 23},
                "Metrotown|drive": None,
            }
        )
        assert format_proximity_caption(mixed) == (
            "23 min to 800 Burrard St; Drive to Metrotown unavailable"
        )
        two_unknown = parse_proximity_display(
            {"Metrotown|drive": None, "downtown|walk": None}
        )
        caption = format_proximity_caption(two_unknown)
        assert caption == "2 proximity criteria unavailable"
        assert "(some unknown)" not in caption

    def test_never_emits_some_unknown(self):
        text = _format_proximity_display(
            {
                "800 Burrard St|drive": {"duration_min": 23},
                "Metrotown|drive": None,
            }
        )
        assert "(some unknown)" not in text
        assert "Distance unknown" not in text

    def test_empty_proximity_is_dash(self):
        assert format_proximity_caption(parse_proximity_display(None)) == "—"
        assert format_proximity_caption(parse_proximity_display({})) == "—"

    def test_table_rows_use_structured_proximity_and_canonical_rank(self):
        rows = _listings_to_table_rows(
            [
                {
                    "id": "b",
                    "rank": 2,
                    "address": "B St",
                    "proximity": {
                        "800 Burrard St|drive": {"duration_min": 23},
                        "Metrotown|drive": None,
                    },
                }
            ]
        )
        assert rows[0]["rank"] == 2
        assert "(some unknown)" not in rows[0]["Proximity"]
        assert "23 min to 800 Burrard St" in rows[0]["Proximity"]


class TestMatchScoreSemantics:
    def test_prefers_match_score_over_semantic(self):
        listing = {"match_score": 0.8, "semantic_score": 0.5}
        assert listing_match_score(listing) == 0.8
        assert _format_match_score(listing) == "80%"

    def test_falls_back_to_semantic_score(self):
        listing = {"semantic_score": 0.5}
        assert listing_match_score(listing) == 0.5
        assert _format_match_score(listing) == "50%"

    def test_missing_or_invalid_score_is_unavailable(self):
        assert listing_match_score({}) is None
        assert _format_match_score({}) == "—"
        assert listing_match_score({"semantic_score": "n/a"}) is None
        assert _format_match_score({"semantic_score": "n/a"}) == "—"


class TestCompactMatchIndicator:
    def test_percentage_and_shared_color(self):
        listing = {"match_score": 0.74}
        html = compact_match_score_html(listing)
        pct, color = match_score_display(listing)
        assert pct == 74
        assert color == get_score_color(74)
        assert "74%" in html
        assert color in html
        assert 'aria-label="Match score: 74 percent"' in html

    def test_semantic_fallback_uses_same_color_helper(self):
        listing = {"semantic_score": 0.5}
        html = compact_match_score_html(listing)
        assert "50%" in html
        assert get_score_color(50) in html

    def test_unavailable_is_accessible_em_dash(self):
        html = compact_match_score_html({})
        assert "—" in html
        assert "Match score: unavailable" in html
        assert "#888888" in html


class TestResultsViewState:
    def test_cards_migrates_to_grid(self):
        assert normalize_results_view("cards") == "grid"
        assert normalize_results_view("Cards") == "grid"

    def test_canonical_and_invalid_views(self):
        assert normalize_results_view("grid") == "grid"
        assert normalize_results_view("table") == "table"
        assert normalize_results_view("map") == "map"
        assert normalize_results_view("unexpected") == "grid"
        assert normalize_results_view(None) == "grid"

    def test_widget_state_migrates_cards_and_keeps_map(self):
        state = {"results_view": "cards"}
        assert prepare_results_widget_state(state) == "grid"
        assert state["results_view"] == "grid"
        state = {"results_view": "map"}
        assert prepare_results_widget_state(state) == "map"
        assert state["results_view"] == "map"


class TestMapLabelState:
    def test_invalid_falls_back_to_price(self):
        assert normalize_map_label_mode("nope") == "price"
        assert normalize_map_label_mode(None) == "price"
        assert normalize_map_label_mode("") == "price"

    def test_canonical_modes_preserved(self):
        assert normalize_map_label_mode("price") == "price"
        assert normalize_map_label_mode("match") == "match"
        assert normalize_map_label_mode("rank") == "rank"
        assert normalize_map_label_mode("PRICE") == "price"

    def test_widget_state_coerces_match_until_match_markers_exist(self):
        state = {"map_label_mode": "match"}
        assert prepare_map_label_widget_state(state) == "price"
        assert state["map_label_mode"] == "price"
        state = {"map_label_mode": "weird"}
        assert prepare_map_label_widget_state(state) == "price"


class TestAddressAndSortHelpers:
    def test_address_split_uses_shared_helper(self):
        headline, locality = listing_address_parts(
            {
                "address": "4137 Dominion Street, Burnaby, British Columbia V5G 1C5",
                "postal_code": "V5G 1C5",
            }
        )
        assert headline == "4137 Dominion Street"
        assert "Burnaby" in locality

    def test_empty_address_is_dash(self):
        headline, locality = listing_address_parts({})
        assert headline == "—"
        assert locality == ""

    def test_sort_by_labels_are_display_only(self):
        assert format_sort_by_label("match_score") == "Match"
        assert format_sort_by_label("semantic_score") == "Match"
        assert format_sort_by_label("proximity") == "Proximity"
        assert format_sort_by_label("price") == "Price"
        assert format_sort_by_label("listing_age_hours") == "Newest"
        assert format_sort_by_label(None) is None
        assert format_sort_by_label("not-a-sort") is None


class TestGridHelpers:
    def test_property_count_label(self):
        assert results_count_label(1) == "1 property"
        assert results_count_label(5) == "5 properties"

    def test_ordered_by_caption_from_last_sort_by(self):
        assert ordered_by_caption("proximity") == "Ordered by proximity"
        assert ordered_by_caption("match_score") == "Ordered by match"
        assert ordered_by_caption("listing_age_hours") == "Ordered by newest"
        assert ordered_by_caption(None) is None
        assert ordered_by_caption("unknown") is None

    def test_property_basics_compact_and_omits_missing(self):
        assert (
            format_property_basics({"bedrooms": 3, "bathrooms": 2, "sqft": 1852})
            == "3 bd · 2 ba · 1,852 sq ft"
        )
        assert format_property_basics({"bedrooms": 3}) == "3 bd"
        assert format_property_basics({"bedrooms_display": "2 + 1", "bathrooms": 2}) == "2 + 1 bd · 2 ba"
        assert format_property_basics({}) == ""
        assert "— ba" not in format_property_basics({"bedrooms": 3})
        assert "— sq ft" not in format_property_basics({"bedrooms": 3})

    def test_grid_proximity_lines_are_structured(self):
        items = parse_proximity_display(
            {
                "800 Burrard St|drive": {"duration_min": 23},
                "nearest transit station|walk": {"duration_min": 2},
                "Metrotown|drive": None,
            }
        )
        lines = proximity_card_lines(items)
        assert ("available", "23 min to 800 Burrard St") in lines
        assert ("available", "2 min walk to transit") in lines
        assert ("unavailable", "ⓘ Drive to Metrotown unavailable") in lines
        assert all("(some unknown)" not in text for _kind, text in lines)

    def test_grid_tags_are_secondary_labels(self):
        labels = listing_tag_labels(
            {"listing_age_hours": 12, "open_house": True, "price_change_display": "↓"}
        )
        assert labels == ["New", "Open house", "Reduced"]
        assert listing_tag_labels({}) == []
