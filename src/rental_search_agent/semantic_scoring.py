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

    Includes: title, address, structured line (bedrooms, bathrooms, sqft, price, lot
    size), description, amenities, nearby_amenities, house_category, property_category,
    open_house. Optionally includes proximity text when the listing has a non-empty
    proximity dict (e.g. after enrich_listings_with_proximity).

    Deliberately excludes fields that aren't preference-relevant text (photo_url,
    video_url, agent_name/agent_phone/brokerage_name, listing_age_display/hours,
    price_change_display) — including them here would add noise to the embedding
    without helping match a listing to a user's stated preferences.
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
    property_category = (_get_listing_attr(listing, "property_category") or "").strip()
    lot_size = (_get_listing_attr(listing, "lot_size") or "").strip()
    open_house = (_get_listing_attr(listing, "open_house") or "").strip()
    parking_spaces = _get_listing_attr(listing, "parking_spaces")
    parking_type = (_get_listing_attr(listing, "parking_type") or "").strip()
    listing_type = _get_listing_attr(listing, "listing_type")

    structured_parts: List[str] = []
    if bedrooms is not None:
        structured_parts.append(f"{int(bedrooms)} bedrooms")
    if bathrooms is not None:
        b = bathrooms
        structured_parts.append(f"{b} bathrooms" if b == int(b) else f"{b} bathrooms")
    if sqft is not None:
        structured_parts.append(f"{int(sqft)} sqft")
    if price is not None:
        # listing_type may be unset on older/hand-built listing dicts; default to rent
        # phrasing only when we don't know, but always use list-price phrasing for sale
        # so for-sale properties aren't misrepresented as monthly rentals to the scorer.
        if listing_type == "for_sale":
            structured_parts.append(f"${int(price)} list price")
        else:
            structured_parts.append(f"${int(price)}/month")
    if parking_spaces is not None:
        parking_desc = f"{int(parking_spaces)} parking space{'s' if int(parking_spaces) != 1 else ''}"
        if parking_type:
            parking_desc += f" ({parking_type})"
        structured_parts.append(parking_desc)
    if lot_size:
        structured_parts.append(f"lot size {lot_size}")
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
    if property_category:
        blob_parts.append(property_category)
    if open_house:
        blob_parts.append(f"Open house: {open_house}")
    if proximity_text:
        blob_parts.append(proximity_text)

    return " ".join(blob_parts)


def _format_count_range(min_val: Optional[float], max_val: Optional[float], unit: str) -> str:
    """Phrase a min/max numeric range (bedrooms, bathrooms, sqft) for the search-criteria
    query blob. unit should already be pluralized (e.g. 'bedrooms').

    Mirrors listing_to_text_blob's own single-value phrasing (e.g. "3 bedrooms") when
    min == max, since an exact request (e.g. "3 bed" -> min_bedrooms=max_bedrooms=3) is the
    most common case and this keeps the query's phrasing lexically identical to how a
    matching listing states its own count, rather than diverging into range language.
    Returns "" when both bounds are None.
    """
    if min_val is None and max_val is None:
        return ""

    def _fmt(v: float) -> str:
        return f"{v:g}"

    if min_val is not None and max_val is not None:
        if min_val == max_val:
            return f"{_fmt(min_val)} {unit}"
        return f"{_fmt(min_val)}-{_fmt(max_val)} {unit}"
    if max_val is not None:
        return f"up to {_fmt(max_val)} {unit}"
    return f"at least {_fmt(min_val)} {unit}"


def _format_price_range(price_min: Optional[float], price_max: Optional[float], listing_type: Optional[str]) -> str:
    """Phrase a price_min/price_max range for the search-criteria query blob, using the same
    for_rent/for_sale suffix convention as listing_to_text_blob's structured_line ("$X/month"
    vs "$X list price") so the query and listing sides phrase price the same way.
    """
    if price_min is None and price_max is None:
        return ""

    def _fmt(v: float) -> str:
        return f"${int(v)}"

    if price_min is not None and price_max is not None and price_min != price_max:
        amount = f"{_fmt(price_min)}-{_fmt(price_max)}"
    elif price_max is not None and price_min is None:
        amount = f"up to {_fmt(price_max)}"
    elif price_min is not None and price_max is None:
        amount = f"at least {_fmt(price_min)}"
    else:
        amount = _fmt(price_min if price_min is not None else price_max)

    if listing_type == "for_sale":
        return f"{amount} list price"
    return f"{amount}/month"


def _proximity_rules_to_query_text(proximity_rules: Optional[List[dict]]) -> str:
    """Phrase parsed ProximityRule dicts (location, mode, max_minutes) as query text, by
    reusing _proximity_to_text() with each rule's max_minutes standing in for duration_min.
    This gives the query the exact same phrasing a listing would use for its own real
    measured distance (e.g. "5 min walk to nearest transit station"), for close lexical
    alignment between the two sides of the comparison. Returns "" for no/empty rules.
    """
    if not proximity_rules:
        return ""
    synthetic_proximity: dict[str, Any] = {}
    for rule in proximity_rules:
        if not isinstance(rule, dict):
            continue
        location = (rule.get("location") or "").strip()
        max_minutes = rule.get("max_minutes")
        if not location or max_minutes is None:
            continue
        mode = (rule.get("mode") or "").strip()
        synthetic_proximity[f"{location}|{mode}"] = {"duration_min": max_minutes}
    return _proximity_to_text(synthetic_proximity)


def search_criteria_to_text_blob(
    criteria: dict[str, Any],
    qualitative_preferences: str = "",
    proximity_rules: Optional[List[dict]] = None,
) -> str:
    """Build a single text representation of the user's current search request, for use as
    the embedding QUERY in score_listings_by_preferences / analyze_listing_against_preferences.

    Deliberately mirrors listing_to_text_blob()'s field choices and phrasing conventions
    (bedroom/bathroom/sqft counts, $-price with the same for_rent/for_sale suffix, and
    proximity phrased via the same _proximity_to_text() helper) so the query text is
    constructed in the same "shape" as the listing text it's compared against, rather than
    being just the bare qualitative_preferences string.

    Args:
        criteria: dict with keys min_bedrooms, max_bedrooms, min_bathrooms, max_bathrooms,
            min_sqft, max_sqft, price_min, price_max, location, listing_type — the same
            shape as RentalSearchFilters/ListingFilterCriteria plus location/listing_type.
        qualitative_preferences: user's qualitative preferences text (e.g. "balcony, parking,
            storage"), included as-is (like a listing's description/amenities text).
        proximity_rules: optional list of parsed ProximityRule dicts (location, mode,
            max_minutes), phrased via _proximity_rules_to_query_text.

    Returns:
        Joined blob string: location, structured bed/bath/sqft/price line, qualitative
        preferences, proximity text — omitting any part with no data.
    """
    location = (criteria.get("location") or "").strip()
    listing_type = criteria.get("listing_type")

    structured_parts: List[str] = []
    beds = _format_count_range(criteria.get("min_bedrooms"), criteria.get("max_bedrooms"), "bedrooms")
    if beds:
        structured_parts.append(beds)
    baths = _format_count_range(criteria.get("min_bathrooms"), criteria.get("max_bathrooms"), "bathrooms")
    if baths:
        structured_parts.append(baths)
    sqft = _format_count_range(criteria.get("min_sqft"), criteria.get("max_sqft"), "sqft")
    if sqft:
        structured_parts.append(sqft)
    price = _format_price_range(criteria.get("price_min"), criteria.get("price_max"), listing_type)
    if price:
        structured_parts.append(price)
    structured_line = ", ".join(structured_parts)

    proximity_text = _proximity_rules_to_query_text(proximity_rules)
    qualitative_preferences = (qualitative_preferences or "").strip()

    blob_parts: List[str] = []
    if location:
        blob_parts.append(location)
    if structured_line:
        blob_parts.append(structured_line)
    if qualitative_preferences:
        blob_parts.append(qualitative_preferences)
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
