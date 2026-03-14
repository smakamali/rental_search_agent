"""Semantic scoring of listings by user preferences via embeddings and cosine similarity."""

import logging
from typing import Any, List, Optional, Union

from rental_search_agent.models import Listing

logger = logging.getLogger(__name__)


def _get_listing_attr(listing: Union[Listing, dict], key: str) -> Any:
    """Get attribute from listing (Listing model or dict)."""
    if isinstance(listing, dict):
        return listing.get(key)
    return getattr(listing, key, None)


def _proximity_to_text(proximity: dict[str, Any]) -> str:
    """Serialize proximity dict to readable text. Keys are 'location|mode', values { distance_km, duration_min }."""
    if not proximity or not isinstance(proximity, dict):
        return ""
    parts: List[str] = []
    for rule_key, val in proximity.items():
        if not isinstance(val, dict):
            continue
        duration_min = val.get("duration_min")
        if duration_min is None:
            continue
        try:
            mins = round(float(duration_min), 0)
        except (TypeError, ValueError):
            continue
        if "|" in rule_key:
            location, mode = rule_key.split("|", 1)
            location = location.strip()
            mode = (mode or "").strip().lower()
            if mode == "drive" or mode == "driving":
                parts.append(f"{int(mins)} min drive to {location}")
            elif mode == "walk" or mode == "walking":
                parts.append(f"{int(mins)} min walk to {location}")
            elif mode == "transit":
                parts.append(f"{int(mins)} min transit to {location}")
            else:
                parts.append(f"{int(mins)} min to {location}")
        else:
            parts.append(f"{int(mins)} min to {rule_key}")
    return ". ".join(parts) if parts else ""


def listing_to_text_blob(listing: Union[Listing, dict]) -> str:
    """Build a single text representation of a listing for embedding.

    Includes: title, address, structured line (bedrooms, bathrooms, sqft, price),
    description, amenities, nearby_amenities, house_category. Optionally includes
    proximity text when the listing has a non-empty proximity dict (e.g. after
    enrich_listings_with_proximity).
    """
    title = (_get_listing_attr(listing, "title") or "").strip()
    address = (_get_listing_attr(listing, "address") or "").strip()
    bedrooms = _get_listing_attr(listing, "bedrooms")
    bathrooms = _get_listing_attr(listing, "bathrooms")
    sqft = _get_listing_attr(listing, "sqft")
    price = _get_listing_attr(listing, "price")
    description = (_get_listing_attr(listing, "description") or "").strip()
    ammenities = (_get_listing_attr(listing, "ammenities") or "").strip()
    nearby_ammenities = (_get_listing_attr(listing, "nearby_ammenities") or "").strip()
    house_category = (_get_listing_attr(listing, "house_category") or "").strip()

    structured_parts: List[str] = []
    if bedrooms is not None:
        structured_parts.append(f"{int(bedrooms)} bedrooms")
    if bathrooms is not None:
        b = bathrooms
        structured_parts.append(f"{b} bathrooms" if b == int(b) else f"{b} bathrooms")
    if sqft is not None:
        structured_parts.append(f"{int(sqft)} sqft")
    if price is not None:
        structured_parts.append(f"${int(price)}/month")
    structured_line = ", ".join(structured_parts) if structured_parts else ""

    proximity = _get_listing_attr(listing, "proximity")
    proximity_text = _proximity_to_text(proximity) if proximity else ""

    blob_parts: List[str] = []
    if title:
        blob_parts.append(title)
    if address:
        blob_parts.append(address)
    if structured_line:
        blob_parts.append(structured_line)
    if description:
        blob_parts.append(description)
    if ammenities:
        blob_parts.append(ammenities)
    if nearby_ammenities:
        blob_parts.append(nearby_ammenities)
    if house_category:
        blob_parts.append(house_category)
    if proximity_text:
        blob_parts.append(proximity_text)

    return " ".join(blob_parts)


def embed_texts(texts: List[str], model: Optional[str] = None) -> List[List[float]]:
    """Embed a list of texts using OpenAI or OpenRouter Embeddings API. Returns list of vectors."""
    if not texts:
        return []
    try:
        from rental_search_agent.api_config import get_embedding_client_and_model
    except ImportError:
        raise ImportError("openai package is required for semantic scoring. pip install openai") from None
    client, default_model = get_embedding_client_and_model()
    embedding_model = model or default_model
    response = client.embeddings.create(input=texts, model=embedding_model)
    ordering = {e.index: e.embedding for e in response.data}
    return [ordering[i] for i in range(len(texts))]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors. Returns value in [-1, 1] (typically [0, 1] for embeddings)."""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def score_listings_by_preferences(
    listings: List[Any],
    preferences_text: str,
    query_text: Optional[str] = None,
    embedding_model: Optional[str] = None,
) -> List[dict]:
    """Score listings by semantic similarity to preferences (and optional query). Returns list of listing dicts with semantic_score added, sorted by score descending. If preferences_text is empty, returns listings unchanged (no scores)."""
    preferences_text = (preferences_text or "").strip()
    query_text = (query_text or "").strip()
    if not preferences_text and not query_text:
        result: List[dict] = []
        for item in listings:
            d = item if isinstance(item, dict) else item.model_dump() if hasattr(item, "model_dump") else dict(item)
            result.append(d)
        return result

    query_str = preferences_text + (" " + query_text if query_text else "")
    query_str = query_str.strip()
    if not query_str:
        result = []
        for item in listings:
            d = item if isinstance(item, dict) else item.model_dump() if hasattr(item, "model_dump") else dict(item)
            result.append(d)
        return result

    blobs = [listing_to_text_blob(item) for item in listings]
    # For empty blobs use a space so we get an embedding; score may be low
    blobs = [b if b.strip() else " " for b in blobs]

    try:
        query_embedding = embed_texts([query_str], model=embedding_model)[0]
        listing_embeddings = embed_texts(blobs, model=embedding_model)
    except Exception as e:
        logger.warning("Semantic scoring embedding failed: %s", e)
        result = []
        for item in listings:
            d = item if isinstance(item, dict) else item.model_dump() if hasattr(item, "model_dump") else dict(item)
            d["semantic_score"] = 0.5
            result.append(d)
        return result

    scored: List[tuple] = []
    for i, item in enumerate(listings):
        d = item if isinstance(item, dict) else item.model_dump() if hasattr(item, "model_dump") else dict(item)
        sim = _cosine_similarity(query_embedding, listing_embeddings[i]) if i < len(listing_embeddings) else 0.0
        # Clamp to [0, 1] for display (embeddings usually give positive similarity)
        sim = max(0.0, min(1.0, sim))
        d["semantic_score"] = round(sim, 4)
        scored.append((d, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [d for d, _ in scored]
