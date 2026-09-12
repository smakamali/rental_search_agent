"""Resolve stored Search Preferences vs chat-extracted criteria (chat wins)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional
import json
import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Contact/booking keys stay persisted but are never shown in Search Preferences UI.
CONTACT_PREF_KEYS = ("viewing_preference", "name", "email", "phone")

SEARCH_PREF_KEYS = (
    "location",
    "listing_type",
    "budget_max",
    "min_bedrooms",
    "max_bedrooms",
    "min_bathrooms",
    "require_den",
    "min_sqft",
    "proximity_preferences",
    "qualitative_preferences",
)

PREF_KEYS = CONTACT_PREF_KEYS + SEARCH_PREF_KEYS


class EffectiveSearchPreferences(BaseModel):
    """Merged preferences used for search, filter, and multi-metric scoring."""

    budget_max: Optional[float] = Field(None, ge=0)
    min_bedrooms: Optional[int] = Field(None, ge=0)
    max_bedrooms: Optional[int] = Field(None, ge=0)
    min_bathrooms: Optional[float] = Field(None, ge=0)
    require_den: Optional[bool] = None
    min_sqft: Optional[float] = Field(None, ge=0)
    house_categories: Optional[list[str]] = None
    proximity_preferences: str = ""
    qualitative_preferences: str = ""
    location: Optional[str] = None
    listing_type: Optional[str] = None
    price_min: Optional[float] = Field(None, ge=0)

    def has_score_relevant_prefs(self) -> bool:
        """True when any component of multi-metric scoring could run."""
        if self.budget_max is not None:
            return True
        if self.min_bedrooms is not None or self.max_bedrooms is not None:
            return True
        if self.min_bathrooms is not None or self.min_sqft is not None:
            return True
        if self.require_den:
            return True
        if self.house_categories:
            return True
        if (self.proximity_preferences or "").strip():
            return True
        if (self.qualitative_preferences or "").strip():
            return True
        return False


def _parse_optional_float(value: Any, *, field: str | None = None) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        if field is not None:
            logger.warning("Failed to parse %s as float: %r", field, value)
        return None


def _parse_optional_int(value: Any, *, field: str | None = None) -> Optional[int]:
    f = _parse_optional_float(value, field=field)
    if f is None:
        return None
    return int(f)


def _parse_optional_bool(value: Any, *, field: str | None = None) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes", "y", "on", "required"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    if field is not None:
        logger.warning("Failed to parse %s as bool: %r", field, value)
    return None


def _parse_listing_type(value: Any, *, field: str | None = None) -> Optional[str]:
    raw = str(value or "").strip()
    s = raw.lower()
    if not s:
        return None
    if s in ("for_rent", "rent", "rental"):
        return "for_rent"
    if s in ("for_sale", "sale", "buy"):
        return "for_sale"
    if field is not None:
        logger.warning("Failed to parse %s as listing_type: %r", field, value)
    return None


def stored_prefs_to_effective(stored: Mapping[str, Any] | None) -> EffectiveSearchPreferences:
    """Parse sidebar/file preference strings into typed EffectiveSearchPreferences."""
    stored = stored or {}
    location = str(stored.get("location") or "").strip() or None
    return EffectiveSearchPreferences(
        budget_max=_parse_optional_float(stored.get("budget_max"), field="budget_max"),
        min_bedrooms=_parse_optional_int(stored.get("min_bedrooms"), field="min_bedrooms"),
        max_bedrooms=_parse_optional_int(stored.get("max_bedrooms"), field="max_bedrooms"),
        min_bathrooms=_parse_optional_float(stored.get("min_bathrooms"), field="min_bathrooms"),
        require_den=_parse_optional_bool(stored.get("require_den"), field="require_den"),
        min_sqft=_parse_optional_float(stored.get("min_sqft"), field="min_sqft"),
        proximity_preferences=str(stored.get("proximity_preferences") or "").strip(),
        qualitative_preferences=str(stored.get("qualitative_preferences") or "").strip(),
        location=location,
        listing_type=_parse_listing_type(stored.get("listing_type"), field="listing_type"),
    )


PLACEHOLDER_QUALITATIVE = frozenset(
    {
        "match my search preferences",
        "match my preferences",
        "search preferences",
    }
)


def is_placeholder_qualitative(text: str | None) -> bool:
    """True for agent/UI placeholder strings that must not become qualitative prefs."""
    s = (text or "").strip().lower()
    if not s:
        return True
    return s in PLACEHOLDER_QUALITATIVE


def qualitative_from_preferences_text(text: str | None) -> str:
    """Return qualitative prefs only: strip a trailing Proximity: block and placeholders.

    Analyze/score tools often receive combined strings like
    'balcony, parking\\n\\nProximity: 5 min walk to transit'. Proximity is scored from
    parsed rules, so that section must not become qualitative_preferences (semantic/amenity).
    """
    s = (text or "").strip()
    if not s:
        return ""
    for sep in ("\n\nProximity:", "\nProximity:"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    if is_placeholder_qualitative(s):
        return ""
    return s


def preferences_file_path() -> Path:
    return Path.home() / ".rental_search_agent" / "preferences.json"


def load_stored_preferences() -> dict[str, str]:
    """Load persisted Search Preferences JSON; empty strings for missing keys."""
    default = {k: "" for k in PREF_KEYS}
    path = preferences_file_path()
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text())
        return {k: data.get(k, "") or "" for k in PREF_KEYS}
    except Exception:
        logger.warning(
            "load_stored_preferences failed; using defaults path=%s",
            path,
            exc_info=True,
        )
        return default


def chat_criteria_to_partial(chat: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize chat/search-criteria dict into fields that can override stored prefs."""
    if not chat:
        return {}
    out: dict[str, Any] = {}
    # price_max from chat maps to budget_max
    budget = chat.get("budget_max")
    if budget is None:
        budget = chat.get("price_max")
    parsed_budget = _parse_optional_float(budget, field="budget_max")
    if parsed_budget is not None:
        out["budget_max"] = parsed_budget

    for key, parser in (
        ("min_bedrooms", _parse_optional_int),
        ("max_bedrooms", _parse_optional_int),
        ("min_bathrooms", _parse_optional_float),
        ("min_sqft", _parse_optional_float),
        ("price_min", _parse_optional_float),
    ):
        if key in chat and chat.get(key) is not None:
            parsed = parser(chat.get(key), field=key)
            if parsed is not None:
                out[key] = parsed

    if "require_den" in chat and chat.get("require_den") is not None:
        parsed_den = _parse_optional_bool(chat.get("require_den"), field="require_den")
        if parsed_den is not None:
            out["require_den"] = parsed_den

    cats = chat.get("house_categories")
    if isinstance(cats, list) and cats:
        cleaned = [str(c).strip() for c in cats if c and str(c).strip()]
        if cleaned:
            out["house_categories"] = cleaned

    for text_key in ("proximity_preferences", "location", "listing_type"):
        if text_key in chat and chat.get(text_key) is not None:
            raw = chat.get(text_key)
            if text_key == "location" and isinstance(raw, list):
                s = ", ".join(str(x).strip() for x in raw if x and str(x).strip())
            else:
                s = str(raw or "").strip()
            if s:
                out[text_key] = s

    # Qualitative: ignore placeholders; only override when real text is provided.
    if "qualitative_preferences" in chat and chat.get("qualitative_preferences") is not None:
        s = str(chat.get("qualitative_preferences") or "").strip()
        if s and not is_placeholder_qualitative(s):
            out["qualitative_preferences"] = s

    return out


def merge_chat_over_stored(
    stored: Mapping[str, Any] | None,
    chat: Mapping[str, Any] | None,
) -> EffectiveSearchPreferences:
    """Chat non-null fields override stored Search Preferences; stored fills the rest."""
    base = stored_prefs_to_effective(stored)
    overrides = chat_criteria_to_partial(chat)
    data = base.model_dump()
    data.update(overrides)
    # If chat set price_max via budget_max and stored had qualitative only, fine.
    effective = EffectiveSearchPreferences(**data)
    set_keys = sorted(k for k, v in effective.model_dump().items() if v not in (None, "", []))
    logger.debug(
        "merge_chat_over_stored: effective_keys=%s n_overrides=%d",
        set_keys,
        len(overrides),
    )
    return effective


def resolve_active_requirement(
    key: str,
    *,
    active_search: Mapping[str, Any] | None = None,
    stored_preferences: Mapping[str, Any] | None = None,
    session_state: Mapping[str, Any] | None = None,
) -> Any:
    """Resolve one search-preference field with active-search precedence.

    Order: current/active search criteria (chat) win over stored preferences.
    ``session_state`` may supply ``user_preferences`` when ``stored_preferences``
    is omitted (Streamlit). There is no separate session search-criteria dict —
    active search is reconstructed from the conversation via the caller.
    """
    stored = stored_preferences
    if stored is None and session_state is not None:
        stored = session_state.get("user_preferences")  # type: ignore[union-attr]
    effective = merge_chat_over_stored(stored, active_search)
    if not hasattr(effective, key):
        raise KeyError(f"Unknown requirement key: {key}")
    return getattr(effective, key)


def fill_empty_stored_from_chat(
    stored: dict[str, str],
    chat: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return a copy of stored prefs with empty search fields filled from chat (no overwrite)."""
    out = {k: str(stored.get(k, "") or "") for k in PREF_KEYS}
    partial = chat_criteria_to_partial(chat)
    string_map = {
        "location": partial.get("location"),
        "listing_type": partial.get("listing_type"),
        "budget_max": partial.get("budget_max"),
        "min_bedrooms": partial.get("min_bedrooms"),
        "max_bedrooms": partial.get("max_bedrooms"),
        "min_bathrooms": partial.get("min_bathrooms"),
        "min_sqft": partial.get("min_sqft"),
        "require_den": partial.get("require_den"),
        "proximity_preferences": partial.get("proximity_preferences"),
        "qualitative_preferences": partial.get("qualitative_preferences"),
    }
    for key, value in string_map.items():
        if value is None:
            continue
        if (out.get(key) or "").strip():
            continue  # never overwrite non-empty stored
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def effective_to_filter_fields(effective: EffectiveSearchPreferences) -> dict[str, Any]:
    """Map effective prefs onto rental_search / filter_listings structural fields."""
    out: dict[str, Any] = {}
    if effective.min_bedrooms is not None:
        out["min_bedrooms"] = effective.min_bedrooms
    if effective.max_bedrooms is not None:
        out["max_bedrooms"] = effective.max_bedrooms
    if effective.min_bathrooms is not None:
        # ListingFilterCriteria expects int; round down for min
        out["min_bathrooms"] = int(effective.min_bathrooms)
    if effective.min_sqft is not None:
        out["min_sqft"] = int(effective.min_sqft)
    if effective.budget_max is not None:
        out["price_max"] = effective.budget_max
    if effective.price_min is not None:
        out["price_min"] = effective.price_min
    if effective.house_categories:
        out["house_categories"] = list(effective.house_categories)
    if effective.location:
        out["location"] = effective.location
    if effective.listing_type:
        out["listing_type"] = effective.listing_type
    return out


def preferences_block(prefs: Mapping[str, Any] | None) -> str:
    """Build the search-relevant preferences block for the system message."""
    prefs = prefs or {}
    parts: list[str] = []
    for key in SEARCH_PREF_KEYS:
        val = str(prefs.get(key) or "").strip()
        if val:
            parts.append(f"{key} = {val!r}")
    if not parts:
        return (
            "No stored search preferences (location, listing type, budget, beds, baths, den, size, proximity, or qualitative)."
        )
    block = "Stored search preferences: " + "; ".join(parts)
    block += (
        ". Treat these as defaults for rental_search / filter_listings / scoring. "
        "Criteria stated in the current chat override stored values for that turn. "
        "When chat fills a field that is empty in stored preferences, persist the filled value. "
        "Do not ask the user for these again unless they are missing or the user asks to change them. "
        "After rental_search, proximity and preference scoring are applied automatically — "
        "do not call parse_proximity_preferences, geocode tools, enrich_listings_with_proximity, "
        "or score_listings_by_preferences for that initial search. "
        "The user can run a new search or re-rank from the Search Preferences Search button. "
        "If a sidebar Search already produced the current master list, do not immediately "
        "replace it unless the user asks for a different search."
    )
    return block
