"""Unit tests for multi-metric match scoring combine + component formulas."""

from unittest.mock import patch

from rental_search_agent.match_scoring import (
    combine_component_scores,
    score_listings_by_preferences,
)
from rental_search_agent.models import Listing
from rental_search_agent.preference_criteria import (
    build_structural_checklist,
    extract_amenity_features,
    score_amenity,
    score_proximity,
    score_structural,
)
from rental_search_agent.preference_resolution import EffectiveSearchPreferences
from rental_search_agent.scoring_config import DEFAULT_WEIGHTS, get_score_weights


def _listing(**kwargs) -> dict:
    base = {
        "id": "1",
        "title": "Test",
        "url": "https://example.com/1",
        "address": "123 Main St",
        "price": 2500.0,
        "bedrooms": 2,
        "bathrooms": 1.0,
        "sqft": 800.0,
        "description": "Bright unit with balcony and parking included.",
        "ammenities": "Balcony, Gym",
        "parking_spaces": 1,
        "has_den": False,
    }
    base.update(kwargs)
    return base


class TestCombine:
    def test_excludes_none_and_renormalizes(self):
        weights = {"structural": 0.5, "proximity": 0.5, "amenity": 0.5, "semantic": 0.5}
        result = combine_component_scores(
            {"structural": 1.0, "proximity": 0.0, "amenity": None, "semantic": None},
            weights,
        )
        assert result == 0.5

    def test_coverage_not_in_overall(self):
        weights = {"structural": 1.0, "proximity": 0, "amenity": 0, "semantic": 0}
        result = combine_component_scores(
            {"coverage": 0.0, "structural": 1.0, "proximity": None, "amenity": None, "semantic": None},
            weights,
        )
        assert result == 1.0

    def test_all_missing_returns_none(self):
        assert (
            combine_component_scores(
                {k: None for k in ("structural", "proximity", "amenity", "semantic")}
            )
            is None
        )


class TestStructuralAndProximity:
    def test_structural_budget_and_beds(self):
        prefs = EffectiveSearchPreferences(budget_max=3000, min_bedrooms=2)
        listing = _listing(price=2500, bedrooms=2)
        score = score_structural(prefs, listing)
        assert score is not None
        assert score >= 0.99

    def test_structural_over_budget_decays(self):
        prefs = EffectiveSearchPreferences(budget_max=2000)
        listing = _listing(price=2500)
        score = score_structural(prefs, listing)
        assert score is not None
        assert 0.0 <= score < 1.0

    def test_proximity_graded(self):
        listing = _listing(
            proximity={"downtown|drive": {"duration_min": 15, "distance_km": 5}}
        )
        rules = [{"location": "downtown", "mode": "drive", "max_minutes": 30}]
        score = score_proximity(listing, rules)
        assert score == 1.0  # under cap = full credit

    def test_proximity_at_limit_is_full_credit(self):
        listing = _listing(
            proximity={"downtown|drive": {"duration_min": 30, "distance_km": 10}}
        )
        rules = [{"location": "downtown", "mode": "drive", "max_minutes": 30}]
        assert score_proximity(listing, rules) == 1.0

    def test_proximity_over_limit_decays(self):
        listing = _listing(
            proximity={"downtown|drive": {"duration_min": 37.5, "distance_km": 12}}
        )
        rules = [{"location": "downtown", "mode": "drive", "max_minutes": 30}]
        score = score_proximity(listing, rules)
        assert score is not None
        assert abs(score - 0.0) < 1e-6  # 1.25× max → 0

    def test_proximity_omitted_when_unknown(self):
        listing = _listing(proximity=None)
        rules = [{"location": "downtown", "mode": "drive", "max_minutes": 30}]
        assert score_proximity(listing, rules) is None


class TestAmenityUnknown:
    def test_missing_text_amenity_is_unknown_when_description_empty(self):
        from rental_search_agent.preference_criteria import (
            AmenityFeature,
            match_amenity_feature,
        )

        listing = _listing(description="", ammenities="", parking_spaces=None)
        result = match_amenity_feature(
            listing, AmenityFeature("balcony", "Balcony", ("balcony",))
        )
        assert result.status == "unknown"
        assert result.score is None

    def test_missing_text_amenity_is_unmet_when_description_present(self):
        from rental_search_agent.preference_criteria import (
            AmenityFeature,
            match_amenity_feature,
        )

        listing = _listing(
            description="Bright apartment near the park. Updated kitchen.",
            ammenities="",
            parking_spaces=None,
        )
        result = match_amenity_feature(
            listing, AmenityFeature("balcony", "Balcony", ("balcony",))
        )
        assert result.status == "unmet"
        assert result.score == 0.0

    def test_description_mention_is_met(self):
        from rental_search_agent.preference_criteria import (
            AmenityFeature,
            match_amenity_feature,
        )

        listing = _listing(
            description="Private balcony and in-suite storage locker.",
            ammenities="",
            parking_spaces=None,
        )
        balcony = match_amenity_feature(
            listing, AmenityFeature("balcony", "Balcony", ("balcony",))
        )
        storage = match_amenity_feature(
            listing, AmenityFeature("storage", "Storage", ("storage", "locker"))
        )
        assert balcony.status == "met"
        assert storage.status == "met"

    def test_description_changes_amenity_and_match_ranking(self):
        prefs = EffectiveSearchPreferences(
            qualitative_preferences="must have balcony, parking, storage",
        )
        with_remarks = _listing(
            id="match",
            description="Corner unit with a balcony, underground parking, and a storage locker.",
            ammenities="",
            parking_spaces=None,
        )
        without_feature = _listing(
            id="miss",
            description="Updated kitchen and hardwood floors in a quiet building.",
            ammenities="",
            parking_spaces=None,
        )
        with patch(
            "rental_search_agent.match_scoring.embed_texts",
            side_effect=RuntimeError("skip semantic"),
        ):
            scored = score_listings_by_preferences(
                [without_feature, with_remarks],
                effective_prefs=prefs,
            )
        by_id = {row["id"]: row for row in scored}
        assert by_id["match"]["score_breakdown"]["components"]["amenity"] == 1.0
        assert by_id["miss"]["score_breakdown"]["components"]["amenity"] == 0.0
        assert scored[0]["id"] == "match"
        assert by_id["match"]["match_score"] > by_id["miss"]["match_score"]


class TestPlaceholderPrefs:
    def test_placeholder_does_not_trigger_semantic(self):
        listings = [_listing()]
        prefs = EffectiveSearchPreferences(
            budget_max=3000,
            qualitative_preferences="match my search preferences",
        )
        with patch("rental_search_agent.match_scoring.embed_texts") as emb:
            scored = score_listings_by_preferences(listings, effective_prefs=prefs)
            emb.assert_not_called()
        assert scored[0]["match_score"] is not None
        assert scored[0]["semantic_score"] is None
        assert "semantic" not in scored[0]["score_breakdown"]["included"]
        assert scored[0]["score_breakdown"].get("coverage") is not None


class TestAmenityExtract:
    def test_extract_features(self):
        feats = extract_amenity_features("I want a balcony, parking, and a gym")
        ids = {f.id for f in feats}
        assert "balcony" in ids
        assert "parking" in ids
        assert "gym" in ids


class TestScoreListings:
    def test_structural_only_no_embedding(self):
        listings = [
            _listing(id="a", price=2000, bedrooms=2),
            _listing(id="b", price=3500, bedrooms=1),
        ]
        prefs = EffectiveSearchPreferences(budget_max=3000, min_bedrooms=2)
        with patch("rental_search_agent.match_scoring.embed_texts") as emb:
            scored = score_listings_by_preferences(
                listings,
                preferences_text="",
                effective_prefs=prefs,
            )
            emb.assert_not_called()
        assert scored[0]["match_score"] is not None
        assert scored[0]["id"] == "a"
        assert scored[0]["match_score"] >= scored[1]["match_score"]
        assert "score_breakdown" in scored[0]
        assert "structural" in scored[0]["score_breakdown"]["included"]
        assert "semantic" not in scored[0]["score_breakdown"]["included"]

    def test_semantic_failure_omits_semantic_keeps_others(self):
        listings = [_listing()]
        prefs = EffectiveSearchPreferences(
            budget_max=3000,
            qualitative_preferences="balcony and parking",
        )
        with patch(
            "rental_search_agent.match_scoring.embed_texts",
            side_effect=RuntimeError("embed down"),
        ):
            scored = score_listings_by_preferences(
                listings, effective_prefs=prefs
            )
        assert scored[0]["match_score"] is not None
        assert scored[0]["semantic_score"] is None
        assert "semantic" not in scored[0]["score_breakdown"]["included"]
        assert "amenity" in scored[0]["score_breakdown"]["included"] or "structural" in scored[0][
            "score_breakdown"
        ]["included"]

    def test_listing_roundtrip_preserves_scores(self):
        listings = [_listing()]
        prefs = EffectiveSearchPreferences(budget_max=3000, min_bedrooms=2)
        scored = score_listings_by_preferences(listings, effective_prefs=prefs)
        model = Listing.model_validate(scored[0])
        assert model.match_score == scored[0]["match_score"]
        assert model.score_breakdown is not None
        dumped = model.model_dump()
        assert dumped["match_score"] == scored[0]["match_score"]
        assert dumped["score_breakdown"]["components"]["structural"] is not None


class TestScoringConfig:
    def test_default_weights(self):
        weights = get_score_weights()
        assert weights == DEFAULT_WEIGHTS
        assert abs(sum(weights.values()) - 1.0) < 1e-6


class TestHouseCategoryCanonicalMatch:
    def test_house_does_not_match_townhouse(self):
        prefs = EffectiveSearchPreferences(house_categories=["House"])
        listing = _listing(house_category="Row / Townhouse")
        item = next(c for c in build_structural_checklist(prefs, listing) if c.id == "house_category")
        assert item.status == "unmet"
        assert score_structural(prefs, listing) == 0.0

    def test_house_matches_house(self):
        prefs = EffectiveSearchPreferences(house_categories=["House"])
        listing = _listing(house_category="House")
        item = next(c for c in build_structural_checklist(prefs, listing) if c.id == "house_category")
        assert item.status == "met"
        assert score_structural(prefs, listing) == 1.0


class TestDenNotDoubleCounted:
    def test_amenity_skips_den_when_required_structurally(self):
        listing = _listing(has_den=True, description="has a den")
        feats = extract_amenity_features("need a den")
        assert any(f.id == "den" for f in feats)
        assert score_amenity(listing, feats) == 1.0
        assert score_amenity(listing, feats, skip_ids={"den"}) is None

    def test_score_listings_omits_amenity_den_when_require_den(self):
        listing = _listing(has_den=True, description="has a den")
        prefs = EffectiveSearchPreferences(require_den=True, qualitative_preferences="den")
        scored = score_listings_by_preferences([listing], effective_prefs=prefs)
        included = scored[0]["score_breakdown"]["included"]
        assert "structural" in included
        assert "amenity" not in included
        den_rows = [c for c in scored[0]["score_breakdown"]["checklist"] if c["id"] == "den"]
        assert len(den_rows) == 1
