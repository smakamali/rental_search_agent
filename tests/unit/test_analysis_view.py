"""Tests for listing-analysis view-model, gauges, and requirement precedence."""

from rental_search_agent.analysis_view import build_analysis_view, checklist_item_to_row
from rental_search_agent.preference_criteria import (
    build_structural_checklist,
    evaluate_coverage,
    extract_amenity_features,
)
from rental_search_agent.preference_resolution import (
    PREF_KEYS,
    merge_chat_over_stored,
    resolve_active_requirement,
)
from rental_search_agent.streamlit_analysis import _gauge_svg_html


def _listing(**kwargs):
    base = {
        "id": "R1",
        "title": "Test",
        "url": "https://example.com/1",
        "address": "3008 939 EXPO BOULEVARD, Vancouver, British Columbia V6Z3G7",
        "postal_code": "V6Z3G7",
        "price": 879_000,
        "bedrooms": 2,
        "bathrooms": 2,
        "sqft": 812,
        "house_category": "Apartment",
        "property_category": "Single Family",
        "description": "Bright apartment with parking",
        "ammenities": "Parking",
        "parking_spaces": 1,
        "proximity": {
            "nearest transit station|walk": {"duration_min": 2, "distance_km": 0.2},
            "800 Burrard st|drive": {"duration_min": 14, "distance_km": 5.0},
        },
    }
    base.update(kwargs)
    return base


class TestRequirementPrecedence:
    def test_active_search_budget_overrides_stored(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "900000"
        value = resolve_active_requirement(
            "budget_max",
            active_search={"price_max": 1_000_000},
            stored_preferences=stored,
        )
        assert value == 1_000_000

        effective = merge_chat_over_stored(stored, {"price_max": 1_000_000})
        items = build_structural_checklist(effective, _listing())
        budget = next(c for c in items if c.id == "budget")
        assert budget.required == "$1,000,000"
        assert "1e" not in (budget.required or "").lower()
        assert budget.observed == "$879,000"


class TestCriteriaDisplay:
    def test_budget_not_scientific_and_comparison(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "1000000"
        stored["min_bedrooms"] = "2"
        stored["min_bathrooms"] = "1"
        stored["min_sqft"] = "750"
        stored["qualitative_preferences"] = "parking, balcony, storage"
        stored["proximity_preferences"] = "walk"
        effective = merge_chat_over_stored(stored, {})
        features = extract_amenity_features(effective.qualitative_preferences)
        rules = [
            {"location": "nearest transit station", "mode": "walk", "max_minutes": 5},
            {"location": "800 Burrard st", "mode": "drive", "max_minutes": 30},
        ]
        _, items = evaluate_coverage(effective, _listing(), rules, features)
        by_id = {c.id: c for c in items}

        budget = by_id["budget"]
        assert budget.observed == "$879,000"
        assert budget.required == "$1,000,000"
        assert "e+" not in budget.required.lower()
        row = checklist_item_to_row({
            "id": budget.id, "name": budget.name, "status": budget.status,
            "observed": budget.observed, "required": budget.required,
            "comparator": budget.comparator, "source": budget.source, "group": budget.group,
        })
        assert row.comparison_text == "$879,000 ≤ $1,000,000"
        assert "(met)" not in row.comparison_text

        beds = by_id["beds"]
        assert checklist_item_to_row({
            "id": beds.id, "name": beds.name, "status": beds.status,
            "observed": beds.observed, "required": beds.required,
            "comparator": beds.comparator, "group": "structural",
        }).comparison_text == "2 ≥ 2"

        walk = next(c for c in items if c.id.startswith("proximity:") and "walk" in c.name.lower())
        assert walk.observed == "2 min"
        assert walk.required == "5 min"
        assert walk.status == "met"
        walk_row = checklist_item_to_row({
            "id": walk.id, "name": walk.name, "status": walk.status,
            "observed": walk.observed, "required": walk.required,
            "comparator": walk.comparator, "source": walk.source, "group": "proximity",
        })
        assert walk_row.comparison_text == "2 min ≤ 5 min"
        assert walk_row.source == "Calculated"

        drive = next(c for c in items if "drive" in c.name.lower())
        drive_row = checklist_item_to_row({
            "id": drive.id, "name": drive.name, "status": drive.status,
            "observed": drive.observed, "required": drive.required,
            "comparator": drive.comparator, "group": "proximity",
        })
        assert drive_row.comparison_text == "14 min ≤ 30 min"

        balcony = by_id["balcony"]
        assert balcony.status == "unknown"
        assert balcony.status != "unmet"
        bal_row = checklist_item_to_row({
            "id": balcony.id, "name": balcony.name, "status": balcony.status,
            "detail": balcony.detail, "group": "amenity",
        })
        assert bal_row.comparison_text == "Not mentioned"
        assert "(unknown)" not in bal_row.comparison_text

        storage = by_id["storage"]
        assert storage.status == "unknown"

        parking = by_id["parking"]
        assert parking.status == "met"


class TestUnknownVsUnmet:
    def test_unmet_is_distinct_from_unknown(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "500"
        stored["qualitative_preferences"] = "balcony"
        effective = merge_chat_over_stored(stored, {})
        features = extract_amenity_features(effective.qualitative_preferences)
        _, items = evaluate_coverage(effective, _listing(price=879_000, description=""), None, features)
        budget = next(c for c in items if c.id == "budget")
        balcony = next(c for c in items if c.id == "balcony")
        assert budget.status == "unmet"
        assert balcony.status == "unknown"

        view = build_analysis_view(
            _listing(),
            {
                "match_score_pct": 40,
                "score_breakdown": {
                    "components": {"structural": 0.0},
                    "included": ["structural"],
                    "weights_used": {"structural": 1.0},
                    "checklist": [
                        {
                            "id": "budget", "group": "structural", "name": "Price",
                            "status": "unmet", "observed": "$879,000",
                            "required": "$500", "comparator": "≤", "source": "MLS",
                        },
                        {
                            "id": "balcony", "group": "amenity", "name": "Balcony",
                            "status": "unknown", "detail": "Not mentioned",
                        },
                    ],
                },
            },
        )
        assert view.unmet_count == 1
        assert view.unknown_count == 1
        assert view.open_questions[0].name == "Balcony"
        assert view.unmet[0].name == "Price"
        assert all(q.status == "unknown" for q in view.open_questions)


class TestAnalysisView:
    def test_missing_values_do_not_crash(self):
        view = build_analysis_view({}, {})
        assert view.headline
        assert view.match_pct is None
        assert view.total_count == 0

    def test_highlights_from_structured_data_not_hardcoded_listing(self):
        listing = _listing()
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "1000000"
        stored["min_bedrooms"] = "2"
        stored["min_bathrooms"] = "1"
        stored["min_sqft"] = "750"
        stored["qualitative_preferences"] = "parking"
        effective = merge_chat_over_stored(stored, {})
        features = extract_amenity_features("parking")
        rules = [{"location": "nearest transit station", "mode": "walk", "max_minutes": 5}]
        _, items = evaluate_coverage(effective, listing, rules, features)
        result = {
            "match_score_pct": 90,
            "score_breakdown": {
                "components": {
                    "structural": 1.0, "proximity": 1.0, "amenity": 1.0, "semantic": 0.52,
                },
                "included": ["structural", "proximity", "amenity", "semantic"],
                "weights_used": {
                    "structural": 0.35, "proximity": 0.25, "amenity": 0.20, "semantic": 0.20,
                },
                "checklist": [
                    {
                        "id": c.id, "name": c.name, "status": c.status, "group": c.group,
                        "observed": c.observed, "required": c.required,
                        "comparator": c.comparator, "source": c.source, "detail": c.detail,
                    }
                    for c in items
                ],
            },
        }
        view = build_analysis_view(listing, result)
        assert view.show_semantic_note
        assert view.evaluated_count >= 1
        assert any("transit" in h.title.lower() or "space" in h.title.lower() or "budget" in h.title.lower()
                   for h in view.highlights)
        joined = " ".join(h.body for h in view.highlights)
        assert "2 min" in joined or "$879,000" in joined or "2 bedrooms" in joined

    def test_property_type_uses_house_category(self):
        from rental_search_agent.analysis_view import listing_property_category, listing_property_type

        listing = _listing()
        assert listing_property_type(listing) == "Apartment"
        assert listing_property_category(listing) == "Single Family"


class TestGaugeHtml:
    def test_percentage_and_aria_visible(self):
        html = _gauge_svg_html(52, "Semantic", size=108)
        assert "52%" in html
        assert 'aria-label="Semantic match score: 52 percent"' in html
        assert "<svg" in html

    def test_missing_score(self):
        html = _gauge_svg_html(None, "Semantic", size=108)
        assert "—" in html
        assert "unavailable" in html
