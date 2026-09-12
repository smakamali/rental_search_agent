"""LLM-based analysis of a single listing against user preferences: match score and key matches/gaps."""

import json
import logging
from typing import Any, Optional, Sequence, Union

from rental_search_agent.agent import current_date_context
from rental_search_agent.api_config import get_llm_client_and_model
from rental_search_agent.match_scoring import score_listings_by_preferences
from rental_search_agent.models import Listing
from rental_search_agent.preference_resolution import (
    EffectiveSearchPreferences,
    is_placeholder_qualitative,
    merge_chat_over_stored,
    qualitative_from_preferences_text,
)
from rental_search_agent.semantic_scoring import listing_to_text_blob

logger = logging.getLogger(__name__)

_MAX_MATCHES = 7
_MAX_GAPS = 7

_ANALYSIS_SYSTEM_PROMPT = (
    "You are an assistant that compares a rental listing to the user's stated preferences. "
    "Return a JSON object with exactly two keys: "
    "'key_matches' (array of short strings: aspects of the listing that satisfy the user's preferences), "
    "and 'key_gaps' (array of short strings: aspects the user wants but the listing lacks or does not mention). "
    "Be concise and factual; only include points clearly supported by the listing text or clearly missing. "
    "If the listing text mentions an open house date, compare it to today's date (given above the listing): "
    "only list it as a key match if the date is today or in the future; if it has already passed, do not mention "
    "it as a match, and do not treat a missing/past open house as a gap unless the user specifically asked for one. "
    "Return only the JSON object with no explanation."
)


def analyze_listing_against_preferences(
    listing: Union[Listing, dict[str, Any]],
    preferences_text: str,
    conversation_context: Optional[str] = None,
    score_query_text: Optional[str] = None,
    *,
    stored_prefs: Optional[dict] = None,
    chat_criteria: Optional[dict] = None,
    proximity_rules: Optional[Sequence[dict]] = None,
    effective_prefs: Optional[EffectiveSearchPreferences] = None,
) -> dict[str, Any]:
    """Analyze one listing against user preferences.

    Numeric match_score_pct uses the same multi-metric scorer as the results table.
    LLM narrative (key_matches / key_gaps) uses preferences_text + optional conversation
    context. score_query_text is accepted for backward compatibility but ignored for the
    numeric score (structured effective prefs drive scoring).

    Returns:
        Dict with: match_score_pct, score_breakdown, key_matches, key_gaps.
    """
    _ = score_query_text  # legacy callers may still pass this; multi-metric path supersedes it
    preferences_text = (preferences_text or "").strip()
    if not preferences_text and effective_prefs is None and not stored_prefs and not chat_criteria:
        raise ValueError("preferences_text is required and must be non-empty.")

    blob = listing_to_text_blob(listing)
    if not blob.strip():
        blob = " "

    chat = dict(chat_criteria or {})
    narrative_source = preferences_text
    qual_from_text = qualitative_from_preferences_text(preferences_text)
    if qual_from_text and not chat.get("qualitative_preferences"):
        chat.setdefault("qualitative_preferences", qual_from_text)

    prefs = effective_prefs or merge_chat_over_stored(stored_prefs, chat)
    if is_placeholder_qualitative(prefs.qualitative_preferences):
        prefs = prefs.model_copy(update={"qualitative_preferences": ""})
    if qual_from_text and not prefs.qualitative_preferences:
        prefs = prefs.model_copy(update={"qualitative_preferences": qual_from_text})

    try:
        scored_list = score_listings_by_preferences(
            [listing],
            preferences_text=prefs.qualitative_preferences or preferences_text or " ",
            effective_prefs=prefs,
            proximity_rules=list(proximity_rules or []),
        )
        scored = scored_list[0] if scored_list else {}
        match_score = scored.get("match_score")
        if match_score is None:
            match_score = scored.get("semantic_score")
        if match_score is None:
            raise ValueError("No match score components could be computed for this listing.")
        match_score_pct = round(float(match_score) * 100)
        score_breakdown = scored.get("score_breakdown")
    except ValueError:
        raise
    except Exception as e:
        logger.warning("Listing analysis scoring failed: %s", e, exc_info=True)
        raise ValueError(f"Failed to compute match score: {e}") from e

    narrative_prefs = narrative_source or prefs.qualitative_preferences or ""
    if not narrative_prefs.strip():
        narrative_prefs = "User search preferences (structured)."

    client, model = get_llm_client_and_model()
    user_content = (
        f"{current_date_context().strip()}\n\nListing:\n{blob}\n\nUser preferences:\n{narrative_prefs}"
    )
    ctx = (conversation_context or "").strip()
    if ctx:
        user_content += f"\n\nAdditional context from conversation:\n{ctx}"
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except Exception as e:
        logger.warning("Listing analysis LLM call failed: %s", e, exc_info=True)
        raise ValueError(f"Failed to analyze listing: {e}") from e

    content = (response.choices[0].message.content or "{}").strip()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("Listing analysis invalid JSON: %r", content)
        raise ValueError(f"Analysis returned invalid JSON: {e}") from e

    key_matches = raw.get("key_matches")
    key_gaps = raw.get("key_gaps")
    if not isinstance(key_matches, list):
        key_matches = []
    if not isinstance(key_gaps, list):
        key_gaps = []
    key_matches = [str(x).strip() for x in key_matches if str(x).strip()][:_MAX_MATCHES]
    key_gaps = [str(x).strip() for x in key_gaps if str(x).strip()][:_MAX_GAPS]

    return {
        "match_score_pct": match_score_pct,
        "score_breakdown": score_breakdown,
        "key_matches": key_matches,
        "key_gaps": key_gaps,
    }
