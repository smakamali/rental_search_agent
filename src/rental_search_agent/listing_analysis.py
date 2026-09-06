"""LLM-based analysis of a single listing against user preferences: match score and key matches/gaps."""

import json
import logging
from typing import Any, Optional, Union

from rental_search_agent.agent import current_date_context
from rental_search_agent.api_config import get_llm_client_and_model
from rental_search_agent.models import Listing
from rental_search_agent.semantic_scoring import (
    _cosine_similarity,
    embed_texts,
    listing_to_text_blob,
)

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
) -> dict[str, Any]:
    """Analyze one listing against user preferences.

    Uses the full listing blob (title, address, description, amenities, etc.) for both
    the semantic match score and the LLM-generated key matches/gaps. The match score
    is computed only from preferences_text and the listing blob; conversation_context
    is optional and used only for the LLM key_matches/key_gaps output.

    Args:
        listing: One listing as dict or Listing model.
        preferences_text: User's qualitative preferences (e.g. balcony, parking, gym).
        conversation_context: Optional summary or excerpt of the conversation; used only
            in the LLM prompt for key_matches/key_gaps, not for the numeric match score.

    Returns:
        Dict with: match_score_pct (int 0-100), key_matches (list[str]), key_gaps (list[str]).

    Raises:
        ValueError: If preferences_text is empty, or on embedding/LLM failure.
    """
    preferences_text = (preferences_text or "").strip()
    if not preferences_text:
        raise ValueError("preferences_text is required and must be non-empty.")

    blob = listing_to_text_blob(listing)
    if not blob.strip():
        blob = " "

    # Match score via embeddings + cosine similarity
    try:
        pref_emb, listing_emb = embed_texts([preferences_text, blob])
        sim = _cosine_similarity(pref_emb, listing_emb)
        sim = max(0.0, min(1.0, sim))
        match_score_pct = round(sim * 100)
    except Exception as e:
        logger.warning("Listing analysis embedding failed: %s", e)
        raise ValueError(f"Failed to compute match score: {e}") from e

    # Key matches / key gaps via LLM. Today's date is injected here (LLM-prompt text
    # only) so the model can reason about stale info like a past open house date —
    # it is deliberately NOT added to `blob` above, since blob also feeds embed_texts()
    # for match_score_pct, and embeddings can't do date arithmetic; adding the date
    # there would just be inert noise in the similarity vector.
    client, model = get_llm_client_and_model()
    user_content = f"{current_date_context().strip()}\n\nListing:\n{blob}\n\nUser preferences:\n{preferences_text}"
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
        logger.warning("Listing analysis LLM call failed: %s", e)
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
        "key_matches": key_matches,
        "key_gaps": key_gaps,
    }
