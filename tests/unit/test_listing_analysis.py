"""Unit tests for rental_search_agent.listing_analysis (multi-metric match score)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.listing_analysis import analyze_listing_against_preferences
from rental_search_agent.preference_resolution import EffectiveSearchPreferences


def _make_llm_response(key_matches: list, key_gaps: list) -> MagicMock:
    content = json.dumps({"key_matches": key_matches, "key_gaps": key_gaps})
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    return mock_resp


def _patch_llm(key_matches=None, key_gaps=None):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_llm_response(
        key_matches or [], key_gaps or []
    )
    return patch(
        "rental_search_agent.listing_analysis.get_llm_client_and_model",
        return_value=(mock_client, "gpt-4o-mini"),
    )


def _sample_listing(**kwargs):
    base = {
        "id": "a",
        "title": "A",
        "url": "https://example.com/a",
        "address": "1 A St",
        "price": 2500.0,
        "bedrooms": 2,
        "bathrooms": 1.0,
        "description": "Has a balcony and parking",
        "ammenities": "Balcony",
        "parking_spaces": 1,
    }
    base.update(kwargs)
    return base


class TestAnalyzeListingAgainstPreferencesValidation:
    def test_empty_preferences_text_raises(self):
        with pytest.raises(ValueError, match="preferences_text"):
            analyze_listing_against_preferences({"id": "a"}, "")

    def test_whitespace_only_preferences_text_raises(self):
        with pytest.raises(ValueError, match="preferences_text"):
            analyze_listing_against_preferences({"id": "a"}, "   ")


class TestAnalyzeMultiMetricScore:
    def test_structural_prefs_produce_match_score_without_embeddings(self):
        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(budget_max=3000, min_bedrooms=2)
        with patch("rental_search_agent.match_scoring.embed_texts") as emb, _patch_llm():
            result = analyze_listing_against_preferences(
                listing,
                "Match my search preferences",
                effective_prefs=prefs,
            )
            emb.assert_not_called()
        assert 0 <= result["match_score_pct"] <= 100
        assert result["score_breakdown"] is not None
        assert "structural" in result["score_breakdown"]["included"]

    def test_table_and_analyze_share_match_score(self):
        from rental_search_agent.match_scoring import score_listings_by_preferences

        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(
            budget_max=3000,
            min_bedrooms=2,
            qualitative_preferences="balcony, parking",
        )

        def fake_embed_texts(texts, model=None):
            return [[1.0, 0.0] for _ in texts]

        with patch("rental_search_agent.match_scoring.embed_texts", side_effect=fake_embed_texts):
            scored = score_listings_by_preferences(
                [listing], effective_prefs=prefs
            )
        table_pct = round(float(scored[0]["match_score"]) * 100)

        with patch(
            "rental_search_agent.match_scoring.embed_texts", side_effect=fake_embed_texts
        ), _patch_llm():
            result = analyze_listing_against_preferences(
                listing,
                "balcony, parking",
                effective_prefs=prefs,
            )
        assert result["match_score_pct"] == table_pct

    def test_returns_key_matches_and_gaps_from_llm(self):
        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(budget_max=3000)
        with patch("rental_search_agent.match_scoring.embed_texts"), _patch_llm(
            key_matches=["Has balcony"], key_gaps=["No gym"]
        ):
            result = analyze_listing_against_preferences(
                listing, "balcony", effective_prefs=prefs
            )
        assert result["key_matches"] == ["Has balcony"]
        assert result["key_gaps"] == ["No gym"]
