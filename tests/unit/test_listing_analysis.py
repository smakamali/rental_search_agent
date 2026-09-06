"""Unit tests for rental_search_agent.listing_analysis."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rental_search_agent.listing_analysis import analyze_listing_against_preferences


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


class TestAnalyzeListingAgainstPreferencesValidation:
    def test_empty_preferences_text_raises(self):
        with pytest.raises(ValueError, match="preferences_text"):
            analyze_listing_against_preferences({"id": "a"}, "")

    def test_whitespace_only_preferences_text_raises(self):
        with pytest.raises(ValueError, match="preferences_text"):
            analyze_listing_against_preferences({"id": "a"}, "   ")


class TestAnalyzeListingAgainstPreferencesScoreQueryText:
    """Regression tests: the match_score_pct shown in the Analyze card must be computed
    from the same query text score_listings_by_preferences uses for the table's
    semantic_score column, otherwise the two "Match score" numbers diverge for the same
    listing/preferences even though the UI labels them identically. score_query_text lets
    a caller decouple the embedding query (used for the numeric score) from the fuller
    preferences_text (used for the LLM key_matches/key_gaps narrative, which may include
    proximity text)."""

    def test_uses_preferences_text_for_score_when_no_override(self):
        listing = {"id": "a", "title": "A", "description": "Has a balcony"}
        captured_texts = {}

        def fake_embed_texts(texts, model=None):
            captured_texts["texts"] = texts
            return [[1.0, 0.0], [1.0, 0.0]]

        with patch("rental_search_agent.listing_analysis.embed_texts", side_effect=fake_embed_texts), _patch_llm():
            result = analyze_listing_against_preferences(listing, "must have balcony")

        assert captured_texts["texts"][0] == "must have balcony"
        assert result["match_score_pct"] == 100

    def test_score_query_text_overrides_preferences_text_for_embedding_only(self):
        listing = {"id": "a", "title": "A", "description": "Has a balcony"}
        captured_texts = {}

        def fake_embed_texts(texts, model=None):
            captured_texts["texts"] = texts
            return [[1.0, 0.0], [1.0, 0.0]]

        combined_preferences = "must have balcony\n\nProximity: 5 min walk to transit"
        with patch("rental_search_agent.listing_analysis.embed_texts", side_effect=fake_embed_texts), _patch_llm():
            result = analyze_listing_against_preferences(
                listing,
                combined_preferences,
                score_query_text="must have balcony",
            )

        # The embedding query used for the score is the override, not the combined text.
        assert captured_texts["texts"][0] == "must have balcony"
        assert result["match_score_pct"] == 100

    def test_score_query_text_matches_score_listings_by_preferences_output(self):
        """End-to-end-ish: given the same override query and blob-affecting listing data,
        analyze_listing_against_preferences's match_score_pct must equal what
        score_listings_by_preferences would compute for the same listing/query, since both
        now build the embedding query from the same qualitative-only text."""
        from rental_search_agent.semantic_scoring import score_listings_by_preferences

        listing = {"id": "a", "title": "A", "description": "Has a balcony and parking"}
        qualitative_only = "must have balcony, parking"
        combined = qualitative_only + "\n\nProximity: 5 min walk to transit"

        # Deterministic fake embeddings: distinct vectors per distinct input text.
        vectors = {
            qualitative_only: [1.0, 0.0, 0.0],
            combined: [0.0, 1.0, 0.0],  # a different query would give a different score
        }

        def fake_embed_texts(texts, model=None):
            out = []
            for t in texts:
                out.append(vectors.get(t, [0.3, 0.3, 0.3]))
            return out

        with patch("rental_search_agent.semantic_scoring.embed_texts", side_effect=fake_embed_texts):
            scored = score_listings_by_preferences([listing], qualitative_only)
        table_score_pct = round(scored[0]["semantic_score"] * 100)

        with patch("rental_search_agent.listing_analysis.embed_texts", side_effect=fake_embed_texts), _patch_llm():
            result = analyze_listing_against_preferences(
                listing,
                combined,
                score_query_text=qualitative_only,
            )

        assert result["match_score_pct"] == table_score_pct

    def test_blank_score_query_text_falls_back_to_preferences_text(self):
        listing = {"id": "a", "title": "A", "description": "Has a balcony"}
        captured_texts = {}

        def fake_embed_texts(texts, model=None):
            captured_texts["texts"] = texts
            return [[1.0, 0.0], [1.0, 0.0]]

        with patch("rental_search_agent.listing_analysis.embed_texts", side_effect=fake_embed_texts), _patch_llm():
            analyze_listing_against_preferences(listing, "must have balcony", score_query_text="   ")

        assert captured_texts["texts"][0] == "must have balcony"


class TestAnalyzeListingAgainstPreferencesKeyMatchesGaps:
    def test_returns_key_matches_and_gaps_from_llm(self):
        listing = {"id": "a", "title": "A", "description": "Has a balcony"}
        with patch(
            "rental_search_agent.listing_analysis.embed_texts",
            return_value=[[1.0, 0.0], [1.0, 0.0]],
        ), _patch_llm(key_matches=["Has balcony"], key_gaps=["No parking mentioned"]):
            result = analyze_listing_against_preferences(listing, "balcony, parking")

        assert result["key_matches"] == ["Has balcony"]
        assert result["key_gaps"] == ["No parking mentioned"]

    def test_embedding_failure_raises_value_error(self):
        listing = {"id": "a", "title": "A"}
        with patch(
            "rental_search_agent.listing_analysis.embed_texts",
            side_effect=Exception("embedding API down"),
        ):
            with pytest.raises(ValueError, match="match score"):
                analyze_listing_against_preferences(listing, "balcony")
