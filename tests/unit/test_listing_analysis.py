"""Unit tests for rental_search_agent.listing_analysis (multi-metric match score)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.listing_analysis import analyze_listing_against_preferences
from rental_search_agent.preference_resolution import EffectiveSearchPreferences


def _make_llm_response(
    key_matches: list,
    key_gaps: list,
    ai_listing_summary: str | None = None,
) -> MagicMock:
    payload = {"key_matches": key_matches, "key_gaps": key_gaps}
    if ai_listing_summary is not None:
        payload["ai_listing_summary"] = ai_listing_summary
    content = json.dumps(payload)
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    return mock_resp


def _patch_llm(key_matches=None, key_gaps=None, ai_listing_summary=None):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_llm_response(
        key_matches or [],
        key_gaps or [],
        ai_listing_summary=ai_listing_summary,
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

    def test_returns_ai_listing_summary_from_llm(self):
        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(budget_max=3000)
        summary = (
            "Well-located 2-bedroom apartment with a balcony. "
            "No gym is indicated."
        )
        with patch("rental_search_agent.match_scoring.embed_texts"), _patch_llm(
            ai_listing_summary=summary
        ):
            result = analyze_listing_against_preferences(
                listing, "balcony", effective_prefs=prefs
            )
        assert result["ai_listing_summary"] == summary

    def test_llm_failure_still_returns_scores_without_summary(self):
        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(budget_max=3000, min_bedrooms=2)
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("rental_search_agent.match_scoring.embed_texts"), patch(
            "rental_search_agent.listing_analysis.get_llm_client_and_model",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = analyze_listing_against_preferences(
                listing,
                "Match my search preferences",
                effective_prefs=prefs,
            )
        assert 0 <= result["match_score_pct"] <= 100
        assert result["score_breakdown"] is not None
        assert result["key_matches"] == []
        assert result["key_gaps"] == []
        assert result["ai_listing_summary"] is None
        mock_client.chat.completions.create.assert_called_once()

    def test_invalid_json_still_returns_scores(self):
        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(budget_max=3000)
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "not-json"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_resp
        with patch("rental_search_agent.match_scoring.embed_texts"), patch(
            "rental_search_agent.listing_analysis.get_llm_client_and_model",
            return_value=(mock_client, "gpt-4o-mini"),
        ):
            result = analyze_listing_against_preferences(
                listing, "balcony", effective_prefs=prefs
            )
        assert result["ai_listing_summary"] is None
        assert result["key_matches"] == []
        assert "match_score_pct" in result

    def test_analyze_does_not_call_llm_twice_when_result_reused(self):
        """Caching is session-level; a stored result must not require a second LLM call."""
        listing = _sample_listing()
        prefs = EffectiveSearchPreferences(budget_max=3000)
        with patch("rental_search_agent.match_scoring.embed_texts"), _patch_llm(
            ai_listing_summary="A balcony apartment."
        ) as llm_patch:
            first = analyze_listing_against_preferences(
                listing, "balcony", effective_prefs=prefs
            )
            # Simulate Streamlit cache reuse: second render uses stored dict, no LLM.
            cached = dict(first)
        assert cached["ai_listing_summary"] == "A balcony apartment."
        llm_patch.assert_called()
        # Client create was invoked only during the first analyze call.
        client = llm_patch.return_value[0]
        assert client.chat.completions.create.call_count == 1
