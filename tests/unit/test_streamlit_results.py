"""Unit tests for search-results presentation helpers."""

from rental_search_agent.display_format import get_score_color
from rental_search_agent.streamlit_results import (
    _build_map_data,
    _format_bedrooms,
    _format_days_on_market,
    _format_listing_price,
    _format_match_score,
    _format_map_price_label,
    _format_proximity_display,
    _listings_to_table_rows,
    compact_match_score_html,
    format_property_basics,
    format_proximity_caption,
    format_sort_by_label,
    listing_address_parts,
    listing_result_identity,
    listing_tag_labels,
    ordered_by_caption,
    proximity_card_lines,
    results_count_label,
    listing_match_score,
    match_score_display,
    current_map_label_mode,
    current_results_view,
    normalize_map_label_mode,
    normalize_results_view,
    parse_proximity_display,
    prepare_map_label_widget_state,
    prepare_results_widget_state,
    proximity_unavailable_summary,
    format_map_coverage,
    table_column_schema,
    table_has_visible_tags,
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

    def test_current_view_does_not_write_after_widget(self):
        state = {"results_view": "table"}
        assert current_results_view(state) == "table"
        assert state["results_view"] == "table"
        stale = {"results_view": "cards"}
        assert current_results_view(stale) == "grid"
        assert stale["results_view"] == "cards"

    def test_prepare_skips_noop_write_when_already_canonical(self):
        writes = []

        class Guard(dict):
            def __setitem__(self, key, value):
                writes.append((key, value))
                super().__setitem__(key, value)

        state = Guard(results_view="grid")
        assert prepare_results_widget_state(state) == "grid"
        assert writes == []


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

    def test_widget_state_keeps_match_and_falls_back_unknown(self):
        state = {"map_label_mode": "match"}
        assert prepare_map_label_widget_state(state) == "match"
        assert state["map_label_mode"] == "match"
        state = {"map_label_mode": "weird"}
        assert prepare_map_label_widget_state(state) == "price"

    def test_current_map_label_does_not_write_after_widget(self):
        state = {"map_label_mode": "rank"}
        assert current_map_label_mode(state) == "rank"
        assert state["map_label_mode"] == "rank"
        stale = {"map_label_mode": "weird"}
        assert current_map_label_mode(stale) == "price"
        assert stale["map_label_mode"] == "weird"

    def test_prepare_map_label_skips_noop_write(self):
        writes = []

        class Guard(dict):
            def __setitem__(self, key, value):
                writes.append((key, value))
                super().__setitem__(key, value)

        state = Guard(map_label_mode="match")
        assert prepare_map_label_widget_state(state) == "match"
        assert writes == []


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


class TestTableHelpers:
    def test_omits_tags_column_when_no_result_has_tags(self):
        listings = [{"id": "a", "rank": 1, "address": "A St"}]
        assert table_has_visible_tags(listings) is False
        assert [col.key for col in table_column_schema(listings)] == [
            "rank",
            "photo",
            "address",
            "type",
            "bed",
            "bath",
            "size",
            "price",
            "dom",
            "match",
            "proximity",
            "analyze",
        ]

    def test_includes_tags_column_when_any_result_has_a_tag(self):
        listings = [
            {"id": "a", "rank": 1},
            {"id": "b", "rank": 2, "listing_age_hours": 12},
        ]
        assert table_has_visible_tags(listings) is True
        assert "tags" in [col.key for col in table_column_schema(listings)]

    def test_size_uses_shared_sqft_formatting(self):
        rows = _listings_to_table_rows(
            [{"id": "a", "address": "A St", "rank": 1, "sqft": 1852}]
        )
        assert rows[0]["size"] == "1,852 sq ft"

    def test_match_prefers_match_score_and_keeps_canonical_rank(self):
        rows = _listings_to_table_rows(
            [
                {
                    "id": "b",
                    "rank": 2,
                    "address": "B St",
                    "match_score": 0.74,
                    "semantic_score": 0.4,
                },
                {
                    "id": "a",
                    "rank": 1,
                    "address": "A St",
                    "semantic_score": 0.9,
                },
            ]
        )
        assert rows[0]["rank"] == 2
        assert rows[0]["match_score"] == "74%"
        assert rows[1]["rank"] == 1
        assert rows[1]["match_score"] == "90%"

    def test_proximity_rows_stay_structured(self):
        rows = _listings_to_table_rows(
            [
                {
                    "id": "a",
                    "rank": 1,
                    "proximity": {
                        "800 Burrard St|drive": {"duration_min": 23},
                        "nearest transit station|walk": {"duration_min": 2},
                        "Metrotown|drive": None,
                    },
                }
            ]
        )
        prox = rows[0]["Proximity"]
        assert "23 min to 800 Burrard St" in prox
        assert "2 min walk to transit" in prox
        assert "(some unknown)" not in prox


class TestMapHelpers:
    def test_price_mode_keeps_compact_currency_and_canonical_rank(self):
        listings = [
            {"id": "b", "rank": 2, "price": 2800, "latitude": 49.28, "longitude": -123.12},
            {"id": "a", "rank": 1, "price": 1_250_000, "latitude": 49.29, "longitude": -123.13},
        ]
        points, _, _ = _build_map_data(listings, label_mode="price")
        assert points[0]["label"] == "$2,800"
        assert points[0]["rank"] == 2
        assert points[1]["label"] == "$1.25M"
        assert points[1]["rank"] == 1
        assert _format_map_price_label({"price": 1_730_000}) == "$1.73M"

    def test_match_mode_prefers_match_score_over_semantic(self):
        listings = [
            {
                "id": "b",
                "rank": 2,
                "match_score": 0.74,
                "semantic_score": 0.4,
                "latitude": 49.28,
                "longitude": -123.12,
            }
        ]
        points, _, _ = _build_map_data(listings, label_mode="match")
        assert points[0]["label"] == "74%"
        assert points[0]["rank"] == 2
        assert points[0]["match_pct"] == 74
        assert points[0]["match_color"] == get_score_color(74)

    def test_match_mode_falls_back_to_semantic_and_missing_is_safe(self):
        listings = [
            {"id": "a", "rank": 1, "semantic_score": 0.88, "latitude": 49.28, "longitude": -123.12},
            {"id": "b", "rank": 3, "latitude": 49.29, "longitude": -123.13},
        ]
        points, _, _ = _build_map_data(listings, label_mode="match")
        assert points[0]["label"] == "88%"
        assert points[1]["label"] == "—"
        assert points[1]["match_pct"] is None
        assert points[1]["rank"] == 3

    def test_rank_mode_uses_canonical_rank_not_position(self):
        listings = [
            {"id": "b", "rank": 2, "latitude": 49.28, "longitude": -123.12},
            {"id": "a", "rank": 1, "latitude": 49.29, "longitude": -123.13},
        ]
        points, _, _ = _build_map_data(listings, label_mode="rank")
        assert points[0]["label"] == "2"
        assert points[1]["label"] == "1"

    def test_invalid_coordinates_skipped_and_coverage_reported(self):
        listings = [
            {"id": "a", "rank": 1, "latitude": 49.28, "longitude": -123.12},
            {"id": "b", "rank": 2},
            {"id": "c", "rank": 3, "latitude": 999, "longitude": 0},
        ]
        points, _, _ = _build_map_data(listings, label_mode="rank")
        assert len(points) == 1
        assert points[0]["rank"] == 1
        assert format_map_coverage(3, len(points)) == (
            "1 of 3 results on map · 2 listings have no mappable location"
        )
        assert format_map_coverage(5, 5) == "5 results on map"
        assert format_map_coverage(1, 1) == "1 result on map"

    def test_rejects_non_http_listing_urls(self):
        listings = [
            {
                "id": "a",
                "rank": 1,
                "url": "javascript:alert(1)",
                "photo_url": "data:image/gif;base64,AAAA",
                "latitude": 49.28,
                "longitude": -123.12,
            }
        ]
        points, _, _ = _build_map_data(listings, label_mode="rank")
        assert points[0]["url"] == ""
        assert points[0]["photo_url"] == ""
        assert _listings_to_table_rows(listings)[0]["URL"] == ""


class TestCrossViewIdentity:
    def test_out_of_order_rank_and_match_are_shared(self):
        listings = [
            {
                "id": "b",
                "rank": 2,
                "match_score": 0.4,
                "semantic_score": 0.9,
                "price": 2800,
                "latitude": 49.28,
                "longitude": -123.12,
            },
            {
                "id": "a",
                "rank": 1,
                "match_score": 0.8,
                "price": 1_730_000,
                "latitude": 49.29,
                "longitude": -123.13,
            },
        ]
        identities = [listing_result_identity(item, i) for i, item in enumerate(listings)]
        rows = _listings_to_table_rows(listings)
        rank_points, _, _ = _build_map_data(listings, label_mode="rank")
        match_points, _, _ = _build_map_data(listings, label_mode="match")

        assert [item["rank"] for item in identities] == [2, 1]
        assert [row["rank"] for row in rows] == [2, 1]
        assert [point["rank"] for point in rank_points] == [2, 1]
        assert [point["label"] for point in rank_points] == ["2", "1"]

        assert identities[0]["match_pct"] == 40
        assert identities[1]["match_pct"] == 80
        assert rows[0]["match_score"] == "40%"
        assert rows[1]["match_score"] == "80%"
        assert match_points[0]["label"] == "40%"
        assert match_points[1]["label"] == "80%"
        assert identities[0]["match_color"] == get_score_color(40)
        assert match_points[0]["match_color"] == get_score_color(40)
        assert rows[0]["price"] == "$2,800"
        assert rows[1]["price"] == "$1,730,000"
        assert _format_map_price_label(listings[1]) == "$1.73M"


class TestMissingDataDisplay:
    def test_never_exposes_none_nan_or_null_tokens(self):
        listing = {
            "id": "x",
            "rank": float("nan"),
            "address": None,
            "postal_code": "null",
            "price": float("nan"),
            "price_display": "NaN",
            "house_category": None,
            "bedrooms": float("nan"),
            "bedrooms_display": "None",
            "bathrooms": None,
            "sqft": float("nan"),
            "match_score": None,
            "semantic_score": "null",
            "listing_age_hours": float("inf"),
            "proximity": None,
        }
        rows = _listings_to_table_rows([listing])
        blob = " ".join(str(value) for value in rows[0].values())
        for token in ("None", "NaN", "nan", "null"):
            assert token not in blob
        assert rows[0]["rank"] == 1
        assert rows[0]["address"] == "—"
        assert rows[0]["type"] == "—"
        assert rows[0]["bed"] == "—"
        assert rows[0]["price"] == "—"
        assert rows[0]["match_score"] == "—"
        assert rows[0]["days_on_market"] == "—"
        assert listing_address_parts(listing)[0] == "—"
        assert format_property_basics(listing) == ""
        assert _format_listing_price(listing) == "—"
        assert _format_bedrooms(listing) == "—"
        assert _format_days_on_market(listing) == "—"
        assert _format_match_score(listing) == "—"
