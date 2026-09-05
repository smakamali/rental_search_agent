"""Chat UI + agent runner. In-process tool execution; ask_user resolved via CLI."""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI

from rental_search_agent.adapter import SearchBackendError, search
from rental_search_agent.calendar_service import default_timezone
from rental_search_agent.agent import current_date_context, flow_instructions, selected_to_listings
from rental_search_agent.filtering import filter_listings as do_filter_listings
from rental_search_agent.geocoding import (
    geocode_location as do_geocode_location,
    geocode_proximity_references as do_geocode_proximity_references,
)
from rental_search_agent.listing_analysis import analyze_listing_against_preferences as do_analyze_listing_against_preferences
from rental_search_agent.models import (
    GeocodedReference,
    Listing,
    ListingFilterCriteria,
    ProximityRule,
    RentalSearchFilters,
)
from rental_search_agent.proximity import enrich_listings_with_proximity as do_enrich_listings_with_proximity
from rental_search_agent.proximity_parser import parse_proximity_preferences as do_parse_proximity_preferences
from rental_search_agent.semantic_scoring import score_listings_by_preferences as do_score_listings_by_preferences
from rental_search_agent.summarizer import summarize_listings as do_summarize_listings
from rental_search_agent.server import (
    calendar_create_event,
    calendar_delete_event,
    calendar_get_available_slots,
    calendar_list_events,
    calendar_update_event,
    do_simulate_viewing_request,
    draft_viewing_plan,
    modify_viewing_plan,
)

# Keys for stored user preferences (same as Streamlit; shared preferences.json)
PREF_KEYS = ("viewing_preference", "name", "email", "phone", "proximity_preferences", "qualitative_preferences")


def _with_display_rank(listings: list[dict]) -> list[dict]:
    """Attach an explicit 1-based 'rank' to each listing dict, matching the exact array
    order the UI renders (see streamlit_app._listings_to_table_rows, which numbers rows
    by position in this same array). Without an explicit field, the LLM has to infer
    'listing N' by counting position in a large embedded JSON blob — which smaller/faster
    models can get wrong (e.g. answering about listing 10 when asked about listing 1).
    Recomputed fresh on every tool result, since filtering/sorting/enrichment changes order.
    """
    out = []
    for i, listing in enumerate(listings):
        d = dict(listing) if isinstance(listing, dict) else listing
        d["rank"] = i + 1
        out.append(d)
    return out


def _preferences_file() -> Path:
    """Path to optional JSON file for persisting preferences (shared with Streamlit)."""
    return Path.home() / ".rental_search_agent" / "preferences.json"


def _load_preferences_from_file() -> dict:
    """Load preferences from file if it exists; otherwise return default dict."""
    default = {k: "" for k in PREF_KEYS}
    path = _preferences_file()
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text())
        return {k: data.get(k, "") or "" for k in PREF_KEYS}
    except Exception:
        return default


def _preferences_block(prefs: dict) -> str:
    """Build the preferences block to inject into the system message (same logic as Streamlit)."""
    viewing = (prefs.get("viewing_preference") or "").strip()
    name = (prefs.get("name") or "").strip()
    email = (prefs.get("email") or "").strip()
    phone = (prefs.get("phone") or "").strip()
    proximity = (prefs.get("proximity_preferences") or "").strip()
    qualitative = (prefs.get("qualitative_preferences") or "").strip()
    if not viewing and not name and not email and not proximity and not qualitative:
        return "No stored user preferences. Ask for viewing preference and for name/email when needed."
    parts = []
    if viewing:
        parts.append(f"viewing_preference = {viewing!r}")
    if name:
        parts.append(f"name = {name!r}")
    if email:
        parts.append(f"email = {email!r}")
    if phone:
        parts.append(f"phone = {phone!r}")
    if proximity:
        parts.append(f"proximity_preferences = {proximity!r}")
    if qualitative:
        parts.append(f"qualitative_preferences = {qualitative!r}")
    block = "Stored user preferences: " + "; ".join(parts)
    block += ". Use these values when calling simulate_viewing_request or when presenting options; do not ask the user for these again unless they are missing or the user asks to change them. When proximity_preferences is set, parse and apply them (parse_proximity_preferences, geocode, enrich_listings_with_proximity, filter_listings with proximity_rules) after presenting search results. When qualitative_preferences is set, use it for scoring/ranking listings (e.g. call score_listings_by_preferences); do not ask again unless the user changes them."
    return block


# Tool definitions for the LLM (OpenAI function-calling format)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user for clarification or approval. Single answer (allow_multiple=False) or multi-select (allow_multiple=True). When asking which listings to request viewings for, you MUST provide choices (one per listing with id, e.g. '[1] 123 Main St — $2800 (id: xyz)') so the user gets a dropdown—never ask for listing numbers in chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Question or instruction shown to the user."},
                    "choices": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Predefined options for dropdown/multiselect. REQUIRED when asking which listings to request viewings for—provide one choice per listing (e.g. '[1] 123 Main St — $2800 (id: xyz)'). Omit only for free-text questions.",
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If true, user may select zero or more choices; if false, single answer.",
                        "default": False,
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rental_search",
            "description": "Run a single property search on Realtor.ca (Canada). Supports for_rent and for_sale. Requires min_bedrooms and location in filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "description": "Search filters: min_bedrooms (int), location (str) required; optional max_bedrooms, min/max_bathrooms, min/max_sqft, price_min, price_max (price bounds: monthly rent for for_rent, list price for for_sale), listing_type (for_rent or for_sale). For exact bedroom count (e.g. '2 bed'), set both min_bedrooms and max_bedrooms. For 'at least N', set only min_bedrooms. Prefer location as 'City, Province' when known (e.g. Vancouver, BC).",
                        "properties": {
                            "min_bedrooms": {"type": "integer", "minimum": 0},
                            "max_bedrooms": {"type": "integer", "minimum": 0},
                            "min_bathrooms": {"type": "integer", "minimum": 0},
                            "max_bathrooms": {"type": "integer", "minimum": 0},
                            "min_sqft": {"type": "integer", "minimum": 0},
                            "max_sqft": {"type": "integer", "minimum": 0},
                            "price_min": {"type": "number", "minimum": 0},
                            "price_max": {"type": "number", "minimum": 0},
                            "location": {"type": "string"},
                            "listing_type": {"type": "string", "enum": ["for_rent", "for_sale"]},
                        },
                        "required": ["min_bedrooms", "location"],
                    },
                },
                "required": ["filters"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_listings",
            "description": "Narrow and/or sort the current search results. Always re-filters from the complete search results (from the most recent rental_search or enrich_listings_with_proximity result), not from a previously filtered subset — so call it again with relaxed or updated criteria instead of running a new rental_search. Pass filter criteria and/or sort_by + ascending and/or proximity_rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_bathrooms": {"type": "integer", "minimum": 0, "description": "Minimum number of bathrooms."},
                    "max_bathrooms": {"type": "integer", "minimum": 0, "description": "Maximum number of bathrooms."},
                    "min_bedrooms": {"type": "integer", "minimum": 0, "description": "Minimum number of bedrooms."},
                    "max_bedrooms": {"type": "integer", "minimum": 0, "description": "Maximum number of bedrooms."},
                    "min_sqft": {"type": "integer", "minimum": 0, "description": "Minimum square footage."},
                    "max_sqft": {"type": "integer", "minimum": 0, "description": "Maximum square footage."},
                    "price_min": {"type": "number", "minimum": 0, "description": "Minimum price (CAD/month for rent; list price for sale)."},
                    "price_max": {"type": "number", "minimum": 0, "description": "Maximum price (CAD/month for rent; list price for sale)."},
                    "sort_by": {"type": "string", "enum": ["price", "bedrooms", "bathrooms", "sqft", "address", "id", "title", "semantic_score", "proximity"], "description": "Attribute to sort by (price, bedrooms, bathrooms, sqft, address, id, title, semantic_score, proximity). Use 'proximity' to sort by nearest first (ascending=true) — requires enrich_listings_with_proximity to have been called. Omit for no sort."},
                    "ascending": {"type": "boolean", "description": "If true, sort ascending (e.g. cheapest first for price, nearest first for proximity). If false, sort descending (e.g. most expensive first). Default true.", "default": True},
                    "proximity_rules": {"type": "array", "items": {"type": "object"}, "description": "Optional. Rules from parse_proximity_preferences; filter to listings satisfying all rules (AND). Listings with unknown proximity are kept."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_listings",
            "description": "Compute statistics (price min/median/mean/max, bedroom distribution, bathroom distribution, size stats, property types) for the current search results. Call when presenting results to produce a structured summary. Uses the most recent rental_search or filter_listings result.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_proximity_preferences",
            "description": "Parse free-text proximity preferences (e.g. 'max 30 min drive to downtown, 5 min walk to transit') into a list of structured rules. Returns { rules: [{ location, mode, max_minutes }, ...] }. Call when the user has set proximity_preferences or stated them in chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proximity_text": {"type": "string", "description": "Free-text proximity preferences from stored prefs or user message."},
                },
                "required": ["proximity_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "geocode_location",
            "description": "Resolve a single location string to coordinates (lat, lon, display_name). Uses Google Geocoding API. Requires GOOGLE_MAPS_API_KEY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Address or place name to geocode."},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "geocode_proximity_references",
            "description": "Geocode all locations from parsed proximity rules. Skips 'nearest transit station'. Pass the rules array from parse_proximity_preferences. Returns { refs: [{ location, lat, lon, display_name }, ...] }. Requires GOOGLE_MAPS_API_KEY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rules": {"type": "array", "items": {"type": "object"}, "description": "List of rule objects from parse_proximity_preferences."},
                },
                "required": ["rules"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enrich_listings_with_proximity",
            "description": "Enrich listings with proximity data (distance_km, duration_min) per rule. The current listings are inferred from context — do NOT pass them as an argument. Only pass rules (from parse_proximity_preferences) and geocoded_refs (from geocode_proximity_references). Returns { listings: [...], total_count } with each listing having a 'proximity' dict. Call after geocode_proximity_references. Requires GOOGLE_MAPS_API_KEY.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rules": {"type": "array", "items": {"type": "object"}, "description": "Rules from parse_proximity_preferences."},
                    "geocoded_refs": {"type": "array", "items": {"type": "object"}, "description": "Refs from geocode_proximity_references."},
                },
                "required": ["rules", "geocoded_refs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_listings_by_preferences",
            "description": "Score and rank listings by semantic similarity to the user's qualitative preferences (e.g. balcony, parking, gym). Pass current listings and preferences_text (from stored qualitative_preferences or user message). Returns listings with semantic_score added, sorted by score descending. Call when qualitative_preferences is set and you have search results to rank.",
            "parameters": {
                "type": "object",
                "properties": {
                    "listings": {"type": "array", "items": {"type": "object"}, "description": "Current listing objects (from rental_search, filter_listings, or enrich_listings_with_proximity)."},
                    "preferences_text": {"type": "string", "description": "User's qualitative/listing preferences (e.g. balcony, parking, gym, pet-friendly). From stored qualitative_preferences or user message."},
                    "query_text": {"type": "string", "description": "Optional. Additional query context (e.g. user's search message) to include when scoring. Omit to use only preferences_text."},
                },
                "required": ["listings", "preferences_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_listing_preferences",
            "description": "Analyze a single listing against the user's preferences. Returns match score (%), key matches (bullets), and key gaps (bullets). Pass the full listing object and a single preferences_text string. When the user has set both listing preferences and proximity preferences, combine them in one string (e.g. list qualitative preferences first, then 'Proximity: ...' with their proximity preferences).",
            "parameters": {
                "type": "object",
                "properties": {
                    "listing": {"type": "object", "description": "Full listing object from current search results (id, title, address, description, etc.)."},
                    "preferences_text": {"type": "string", "description": "User's preferences as one string: listing/qualitative preferences (e.g. balcony, parking, gym) and optionally proximity (e.g. 'Proximity: max 30 min drive to downtown'). Combine both from stored qualitative_preferences and proximity_preferences when set."},
                },
                "required": ["listing", "preferences_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_viewing_request",
            "description": "Simulate a viewing request (no real form POST). Use listing url, a timeslot string from user's preference, and user_details (name, email required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "listing_url": {"type": "string", "description": "Canonical URL of the listing."},
                    "timeslot": {"type": "string", "description": "Human-readable timeslot (e.g. Tuesday 6–8pm)."},
                    "user_details": {
                        "type": "object",
                        "description": "User details: name and email required; phone and preferred_times optional.",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "preferred_times": {"type": "string"},
                        },
                        "required": ["name", "email"],
                    },
                },
                "required": ["listing_url", "timeslot", "user_details"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_get_available_slots",
            "description": "Get available calendar slots within user's preferred viewing times. Call BEFORE drafting a viewing plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preferred_times": {"type": "string", "description": "User's viewing preference (e.g. weekday evenings 6-8pm)."},
                    "date_range_start": {"type": "string", "description": "ISO datetime for start. Optional: defaults to tomorrow 00:00 when omitted."},
                    "date_range_end": {"type": "string", "description": "ISO datetime for end. Optional: defaults to 2 weeks from today 23:59 when omitted."},
                    "slot_duration_minutes": {"type": "integer", "description": "Slot length in minutes.", "default": 60},
                },
                "required": ["preferred_times"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_viewing_plan",
            "description": "REQUIRED after calendar_get_available_slots: Draft a viewing plan by assigning slots to listings (clusters nearby listings). Call this tool immediately when slots are returned—do not respond to the user until you have called it. Pass listings (selected from step 6) and available_slots (from calendar_get_available_slots). Returns entries with start_datetime, end_datetime (ISO), slot_display, and unused_slots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "listings": {"type": "array", "items": {"type": "object"}, "description": "Selected listings with id, address, url, latitude, longitude."},
                    "available_slots": {"type": "array", "items": {"type": "object"}, "description": "Slots from calendar_get_available_slots."},
                },
                "required": ["listings", "available_slots"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_viewing_plan",
            "description": "Modify the viewing plan when the user wants changes in Step 11. Supports: remove (listing IDs to remove), add (listings to add with their slot: [{listing_id, listing_address, listing_url, slot: {start, end, display}}]), update (change slot for listing: [{listing_id, new_slot: {start, end, display}}]). Current plan entries and available_slots come from prior tool results. Use unused_slots from the plan response to pick valid slots for add/update.",
            "parameters": {
                "type": "object",
                "properties": {
                    "remove": {"type": "array", "items": {"type": "string"}, "description": "Listing IDs to remove from the plan."},
                    "add": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "listing_id": {"type": "string"},
                                "listing_address": {"type": "string"},
                                "listing_url": {"type": "string"},
                                "slot": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "display": {"type": "string"}}},
                            },
                        },
                        "description": "Listings to add: each needs listing_id, listing_address, listing_url, slot (from unused_slots).",
                    },
                    "update": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "listing_id": {"type": "string"},
                                "new_slot": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "display": {"type": "string"}}},
                            },
                        },
                        "description": "Change slot for listing: each needs listing_id and new_slot (from unused_slots).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Create a calendar event for a viewing. Use start_datetime and end_datetime from draft_viewing_plan entry (ISO format e.g. 2026-03-02T18:00:00). Never use slot_display for these fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title (e.g. Property viewing: 123 Main St)."},
                    "start_datetime": {"type": "string", "description": "ISO datetime for start (e.g. 2026-03-02T18:00:00) from plan entry."},
                    "end_datetime": {"type": "string", "description": "ISO datetime for end (e.g. 2026-03-02T19:00:00) from plan entry."},
                    "description": {"type": "string", "description": "Optional description."},
                    "location": {"type": "string", "description": "Optional location."},
                    "listing_id": {"type": "string", "description": "Listing ID for update flow."},
                    "listing_url": {"type": "string", "description": "Listing URL for update flow."},
                },
                "required": ["summary", "start_datetime", "end_datetime"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_update_event",
            "description": "Update an existing calendar event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Event ID to update."},
                    "summary": {"type": "string"},
                    "start_datetime": {"type": "string"},
                    "end_datetime": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_delete_event",
            "description": "Delete a calendar event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Event ID to delete."},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_list_events",
            "description": "List calendar events in a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "ISO datetime for start."},
                    "time_max": {"type": "string", "description": "ISO datetime for end."},
                    "calendar_id": {"type": "string", "description": "Calendar ID.", "default": "primary"},
                    "max_results": {"type": "integer", "description": "Max events to return.", "default": 50},
                },
                "required": ["time_min", "time_max"],
            },
        },
    },
]


def _get_current_listings_from_messages(messages: list[dict]) -> list[dict]:
    """Return the listings array from the most recent tool result that has 'listings' (rental_search or filter_listings)."""
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        try:
            data = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "listings" in data:
            raw = data.get("listings")
            if isinstance(raw, list):
                return raw
        if isinstance(data, dict) and "error" in data:
            continue
        # Tool results like ask_user {answer}/{selected} don't contain listings; keep looking.
        continue
    return []


def _get_listings_from_tool(messages: list[dict], tool_name: str) -> list[dict]:
    """Return listings from the most recent result of a specific tool, identified by tool_call_id mapping."""
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            tc_id = tc.get("id")
            name = (tc.get("function") or {}).get("name")
            if tc_id and name:
                id_to_name[tc_id] = name
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        if id_to_name.get(msg.get("tool_call_id")) != tool_name:
            continue
        try:
            data = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("listings"), list):
            return data["listings"]
    return []


def _get_master_listings_from_messages(messages: list[dict]) -> list[dict]:
    """Return listings from the most recent rental_search result (the master set)."""
    return _get_listings_from_tool(messages, "rental_search")


def _tool_result_message_index(messages: list[dict], tool_name: str) -> int | None:
    """Return the index of the most recent tool result message for tool_name, or None."""
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            tc_id = tc.get("id")
            name = (tc.get("function") or {}).get("name")
            if tc_id and name:
                id_to_name[tc_id] = name
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "tool":
            continue
        if id_to_name.get(msg.get("tool_call_id")) == tool_name:
            return i
    return None


def _get_enriched_master_from_messages(messages: list[dict]) -> list[dict]:
    """Return listings from the most recent enrich_listings_with_proximity result.

    Ignores enrich results that predate the latest rental_search so a new search
    does not keep using proximity data from a previous search.
    """
    enrich_idx = _tool_result_message_index(messages, "enrich_listings_with_proximity")
    if enrich_idx is None:
        return []
    search_idx = _tool_result_message_index(messages, "rental_search")
    if search_idx is not None and search_idx > enrich_idx:
        return []
    try:
        data = json.loads(messages[enrich_idx].get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("listings"), list):
        return data["listings"]
    return []


def _last_completed_tool_name(messages: list[dict]) -> str | None:
    """Return the name of the last tool that was executed (from the most recent assistant message with tool_calls)."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls") or []
        if not tcs:
            continue
        last_tc = tcs[-1]
        fn = last_tc.get("function") or {}
        name = fn.get("name")
        if name:
            return name
    return None


def _get_available_slots_from_messages(messages: list[dict]) -> list[dict]:
    """Return available_slots from the most recent calendar_get_available_slots result."""
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        try:
            data = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "slots" in data:
            raw = data.get("slots")
            if isinstance(raw, list):
                return raw
    return []


def _get_viewing_plan_from_messages(messages: list[dict]) -> list[dict]:
    """Return entries from the most recent draft_viewing_plan or modify_viewing_plan tool result."""
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        try:
            data = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "entries" in data:
            raw = data.get("entries")
            if isinstance(raw, list):
                return raw
    return []


def _get_selected_listings_from_messages(messages: list[dict]) -> list[dict]:
    """Return selected listing dicts from the ask_user listing-selection step (choices with id)."""
    current_listings_raw: list[dict] = []
    selected: list[str] = []
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        try:
            data = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if "listings" in data:
            raw = data.get("listings")
            if isinstance(raw, list):
                current_listings_raw = raw
        if "selected" in data:
            sel = data.get("selected")
            if isinstance(sel, list) and sel and any("(id: " in str(s) for s in sel):
                selected = sel
    if not selected or not current_listings_raw:
        return []
    try:
        shortlist = [Listing.model_validate(x) for x in current_listings_raw]
        listings = selected_to_listings(selected, shortlist)
        return [lst.model_dump() for lst in listings]
    except Exception:
        return []


def run_tool(
    name: str,
    arguments: dict,
    *,
    current_listings: list[dict] | None = None,
    current_plan_entries: list[dict] | None = None,
    available_slots: list[dict] | None = None,
) -> str:
    """Execute tool in-process and return JSON string result. For ask_user, returns request_user_input payload; caller must resolve via UI and pass back answer/selected."""
    if name == "ask_user":
        # Return payload for client to show UI and supply real result
        return json.dumps({
            "request_user_input": True,
            "prompt": arguments.get("prompt", ""),
            "choices": arguments.get("choices") or [],
            "allow_multiple": arguments.get("allow_multiple", False),
        })
    if name == "rental_search":
        try:
            f = RentalSearchFilters.model_validate(arguments["filters"])
        except Exception as e:
            return json.dumps({"error": f"Invalid filters: {e}"})
        try:
            resp = search(f)
        except SearchBackendError as e:
            return json.dumps({"error": str(e)})
        data = resp.model_dump()
        data["listings"] = _with_display_rank(data["listings"])
        return json.dumps(data)
    if name == "filter_listings":
        listings = current_listings if current_listings is not None else []
        if not listings:
            return json.dumps({"error": "No current search results to filter or sort. Run a search first."})
        sort_by = arguments.get("sort_by")
        ascending = arguments.get("ascending", True)
        criteria_keys = {"min_bathrooms", "max_bathrooms", "min_bedrooms", "max_bedrooms", "min_sqft", "max_sqft", "price_min", "price_max"}
        criteria_dict = {k: v for k, v in arguments.items() if k in criteria_keys and v is not None}
        proximity_rules_raw = arguments.get("proximity_rules") or []
        if not criteria_dict and not sort_by and not proximity_rules_raw:
            return json.dumps({"error": "At least one filter criterion, sort_by, or proximity_rules is required."})
        # Guard: proximity filtering requires enriched listings. If rules are supplied but no
        # listing has proximity data, the filter will silently pass everything through.
        if proximity_rules_raw and not any(
            isinstance(lst, dict) and lst.get("proximity") for lst in listings
        ):
            return json.dumps({
                "error": (
                    "Listings have not been enriched with proximity data yet. "
                    "Call enrich_listings_with_proximity(listings, rules, geocoded_refs) first, "
                    "then retry filter_listings with proximity_rules."
                )
            })
        criteria = ListingFilterCriteria.model_validate(criteria_dict) if criteria_dict else ListingFilterCriteria()
        proximity_rules = [ProximityRule.model_validate(r) for r in proximity_rules_raw] if proximity_rules_raw else None
        resp = do_filter_listings(listings, criteria, sort_by=sort_by, ascending=ascending, proximity_rules=proximity_rules)
        data = resp.model_dump()
        data["listings"] = _with_display_rank(data["listings"])
        return json.dumps(data)
    if name == "summarize_listings":
        listings = current_listings if current_listings is not None else []
        if not listings:
            return json.dumps({"error": "No current search results to summarize. Run a search first."})
        result = do_summarize_listings(listings)
        return json.dumps(result)
    if name == "parse_proximity_preferences":
        text = (arguments.get("proximity_text") or "").strip()
        rules = do_parse_proximity_preferences(text)
        return json.dumps({"rules": [r.model_dump() for r in rules]})
    if name == "geocode_location":
        try:
            loc = (arguments.get("location") or "").strip()
            if not loc:
                return json.dumps({"error": "location is required and must be non-empty."})
            ref = do_geocode_location(loc)
            return json.dumps(ref.model_dump())
        except ValueError as e:
            return json.dumps({"error": str(e)})
    if name == "geocode_proximity_references":
        try:
            raw_rules = arguments.get("rules") or []
            rule_objs = [ProximityRule.model_validate(r) for r in raw_rules]
            refs = do_geocode_proximity_references(rule_objs)
            return json.dumps({"refs": [r.model_dump() for r in refs]})
        except Exception as e:
            return json.dumps({"error": str(e)})
    if name == "enrich_listings_with_proximity":
        try:
            # Prefer in-memory current_listings (passed via run_agent_step) so the LLM
            # does not need to echo the full listing JSON in its tool call arguments.
            listings = (current_listings if current_listings is not None else []) or arguments.get("listings") or []
            rules_raw = arguments.get("rules") or []
            refs_raw = arguments.get("geocoded_refs") or []
            if not listings:
                return json.dumps({"error": "No current listings to enrich. Run a search (or filter) first."})
            rule_objs = [ProximityRule.model_validate(r) for r in rules_raw]
            ref_objs = [GeocodedReference.model_validate(r) for r in refs_raw]
            enriched = do_enrich_listings_with_proximity(listings, rule_objs, ref_objs)
            return json.dumps({"listings": _with_display_rank(enriched), "total_count": len(enriched)})
        except Exception as e:
            return json.dumps({"error": str(e)})
    if name == "score_listings_by_preferences":
        listings = current_listings if current_listings is not None else []
        if not listings:
            return json.dumps({"error": "No current search results to score. Run a search first."})
        preferences_text = (arguments.get("preferences_text") or "").strip()
        if not preferences_text:
            return json.dumps({"error": "preferences_text is required and must be non-empty."})
        try:
            scored = do_score_listings_by_preferences(
                listings,
                preferences_text,
                query_text=(arguments.get("query_text") or "").strip() or None,
            )
            return json.dumps({"listings": _with_display_rank(scored), "total_count": len(scored)})
        except Exception as e:
            return json.dumps({"error": str(e)})
    if name == "analyze_listing_preferences":
        listing = arguments.get("listing")
        preferences_text = (arguments.get("preferences_text") or "").strip()
        if not listing or not isinstance(listing, dict):
            return json.dumps({"error": "listing is required and must be a non-empty object."})
        if not preferences_text:
            return json.dumps({"error": "preferences_text is required and must be non-empty."})
        try:
            result = do_analyze_listing_against_preferences(listing, preferences_text)
            return json.dumps(result)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": str(e)})
    if name == "simulate_viewing_request":
        try:
            resp = do_simulate_viewing_request(
                arguments["listing_url"],
                arguments["timeslot"],
                arguments["user_details"],
            )
            return resp.model_dump_json()
        except ValueError as e:
            return json.dumps({"error": str(e)})
    if name == "calendar_get_available_slots":
        try:
            logger.debug("calendar_get_available_slots: computing date range")
            tz = ZoneInfo(default_timezone())
            now = datetime.now(tz)
            tomorrow = (now.date() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            two_weeks = (now.date() + timedelta(days=14)).strftime("%Y-%m-%dT23:59:59")
            date_range_start = (arguments.get("date_range_start") or "").strip() or tomorrow
            date_range_end = (arguments.get("date_range_end") or "").strip() or two_weeks
            logger.debug("calendar_get_available_slots: calling API (start=%s end=%s)", date_range_start, date_range_end)
            result = calendar_get_available_slots(
                arguments["preferred_times"],
                date_range_start,
                date_range_end,
                slot_duration_minutes=arguments.get("slot_duration_minutes", 60),
            )
            logger.debug("calendar_get_available_slots: returned %d slots", len(result.get("slots", [])))
            return json.dumps(result)
        except ValueError as e:
            logger.debug("calendar_get_available_slots: error %s", e)
            return json.dumps({"error": str(e)})
    if name == "draft_viewing_plan":
        try:
            listings_count = len(arguments.get("listings") or [])
            slots_count = len(arguments.get("available_slots") or [])
            logger.debug("draft_viewing_plan: %d listings, %d slots", listings_count, slots_count)
            result = draft_viewing_plan(
                arguments["listings"],
                arguments["available_slots"],
            )
            logger.debug("draft_viewing_plan: created %d entries", len(result.get("entries", [])))
            return json.dumps(result)
        except ValueError as e:
            logger.debug("draft_viewing_plan: error %s", e)
            return json.dumps({"error": str(e)})
    if name == "modify_viewing_plan":
        plan_entries = current_plan_entries if current_plan_entries is not None else []
        slots = available_slots if available_slots is not None else []
        if not plan_entries:
            return json.dumps({"error": "No current viewing plan to modify. Draft a plan first."})
        if not slots:
            return json.dumps({"error": "No available slots in context. Run calendar_get_available_slots first."})
        try:
            result = modify_viewing_plan(
                plan_entries,
                slots,
                remove=arguments.get("remove") or [],
                add=arguments.get("add") or [],
                update=arguments.get("update") or [],
            )
            logger.debug("modify_viewing_plan: %d entries", len(result.get("entries", [])))
            return json.dumps(result)
        except ValueError as e:
            logger.debug("modify_viewing_plan: error %s", e)
            return json.dumps({"error": str(e)})
    if name == "calendar_create_event":
        try:
            start_dt = arguments.get("start_datetime")
            end_dt = arguments.get("end_datetime")
            if not start_dt or not end_dt:
                return json.dumps({
                    "error": "start_datetime and end_datetime are required (ISO format from draft_viewing_plan entry, e.g. 2026-03-02T18:00:00)."
                })
            logger.debug("calendar_create_event: %s at %s", (arguments.get("summary") or "")[:40], start_dt)
            result = calendar_create_event(
                summary=arguments.get("summary") or "Property viewing",
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=arguments.get("description"),
                location=arguments.get("location"),
                listing_id=arguments.get("listing_id"),
                listing_url=arguments.get("listing_url"),
            )
            logger.debug("calendar_create_event: created %s", result.get("id"))
            return json.dumps(result)
        except ValueError as e:
            logger.debug("calendar_create_event: error %s", e)
            return json.dumps({"error": str(e)})
    if name == "calendar_update_event":
        try:
            result = calendar_update_event(
                event_id=arguments["event_id"],
                summary=arguments.get("summary"),
                start_datetime=arguments.get("start_datetime"),
                end_datetime=arguments.get("end_datetime"),
                description=arguments.get("description"),
                location=arguments.get("location"),
            )
            return json.dumps(result)
        except ValueError as e:
            return json.dumps({"error": str(e)})
    if name == "calendar_delete_event":
        try:
            result = calendar_delete_event(arguments["event_id"])
            return json.dumps(result)
        except ValueError as e:
            return json.dumps({"error": str(e)})
    if name == "calendar_list_events":
        try:
            result = calendar_list_events(
                time_min=arguments["time_min"],
                time_max=arguments["time_max"],
                calendar_id=arguments.get("calendar_id", "primary"),
                max_results=arguments.get("max_results", 50),
            )
            return json.dumps(result)
        except ValueError as e:
            return json.dumps({"error": str(e)})
    return json.dumps({"error": f"Unknown tool: {name}"})


def prompt_user_for_ask_user(payload: dict) -> str:
    """Show prompt and choices in CLI; return JSON string of { answer } or { selected }."""
    prompt = payload.get("prompt", "")
    choices = payload.get("choices") or []
    allow_multiple = payload.get("allow_multiple", False)
    print("\n--- " + prompt + " ---")
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        if allow_multiple:
            print("Enter numbers separated by commas (e.g. 1,3), or 0 for none:")
        else:
            print("Enter number or your answer:")
    else:
        print("Enter your answer:")
    line = (sys.stdin.readline() or "").strip()
    if allow_multiple:
        if not line or line == "0":
            return json.dumps({"selected": []})
        try:
            indices = [int(x.strip()) for x in line.split(",")]
            selected = [choices[i - 1] for i in indices if 1 <= i <= len(choices)]
            return json.dumps({"selected": selected})
        except (ValueError, IndexError):
            return json.dumps({"selected": []})
    if choices and line.isdigit():
        idx = int(line)
        if 1 <= idx <= len(choices):
            return json.dumps({"answer": choices[idx - 1]})
    return json.dumps({"answer": line})


_DEBUG_LOGGING_SETUP = False


def _setup_debug_logging(project_root: Path) -> None:
    """Configure file-based debug logging so output appears even if stderr is captured (e.g. by Streamlit)."""
    global _DEBUG_LOGGING_SETUP
    if _DEBUG_LOGGING_SETUP:
        return
    _DEBUG_LOGGING_SETUP = True
    log_file = project_root / "rental_search_agent_debug.log"
    handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    pkg_logger = logging.getLogger("rental_search_agent")
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(logging.DEBUG)


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from path into os.environ if not already set."""
    project_root = path.parent if path.name == ".env" else Path(__file__).resolve().parent.parent.parent
    _setup_debug_logging(project_root)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


def _make_llm_client() -> tuple[OpenAI, str]:
    """Build LLM client and model name from unified API config (api_config)."""
    try:
        from rental_search_agent.api_config import get_llm_client_and_model
        return get_llm_client_and_model()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        print("See https://openrouter.ai for OpenRouter or set OPENAI_API_KEY for OpenAI.", file=sys.stderr)
        sys.exit(1)


logger = logging.getLogger(__name__)


def _listing_state_from_messages(messages: list[dict]) -> dict | None:
    """Build listing_state (display_list, master_list, display_source) from message history."""
    display_list = _get_current_listings_from_messages(messages)
    master_list = _get_enriched_master_from_messages(messages) or _get_master_listings_from_messages(messages)
    last_tool = _last_completed_tool_name(messages)
    display_source = None
    if last_tool == "rental_search":
        display_source = "search"
    elif last_tool == "filter_listings":
        display_source = "filter"
    elif last_tool == "enrich_listings_with_proximity":
        display_source = "enrich"
    elif last_tool == "score_listings_by_preferences":
        display_source = "score"
    return {"display_list": display_list or [], "master_list": master_list or [], "display_source": display_source}


def run_agent_step(client: OpenAI, model: str, messages: list[dict]) -> tuple[list[dict], dict | None, dict | None]:
    """Run one or more LLM calls and tool executions. Returns (updated_messages, ask_user_payload | None, listing_state | None).
    listing_state has display_list, master_list, display_source for the UI. When ask_user needs input, returns
    (messages + assistant_msg + tool_results_before_ask, payload, listing_state)."""
    last_listing_state: dict | None = None
    while True:
        logger.debug("Calling LLM (model=%s)...", model)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        if not resp or not resp.choices:
            logger.warning("LLM returned empty choices (possible API error or context too long); aborting step.")
            return (messages, None, last_listing_state or _listing_state_from_messages(messages))
        msg = resp.choices[0].message
        logger.debug("LLM responded")
        if not msg:
            return (messages, None, _listing_state_from_messages(messages))
        if msg.tool_calls:
            logger.debug("LLM requested %d tool(s): %s", len(msg.tool_calls), [tc.function.name for tc in msg.tool_calls])
            assistant_msg = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                    }
                    for tc in msg.tool_calls
                ],
            }
            tool_results: list[dict] = []
            current_listings = _get_current_listings_from_messages(messages)
            master_listings = _get_master_listings_from_messages(messages)
            enriched_master = _get_enriched_master_from_messages(messages)
            display_source: str | None = None
            current_plan_entries = _get_viewing_plan_from_messages(messages)
            available_slots = _get_available_slots_from_messages(messages)
            for tc in msg.tool_calls:
                name = tc.function.name
                logger.debug("Executing tool: %s", name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                # filter_listings always re-filters from the master (enriched if available, else raw)
                # so that relaxing filters never requires a new rental_search call.
                # summarize_listings uses current_listings (the latest filtered view for same-turn chaining).
                if name == "filter_listings":
                    filter_source = enriched_master or master_listings
                elif name == "summarize_listings":
                    filter_source = current_listings
                elif name == "score_listings_by_preferences":
                    filter_source = enriched_master or master_listings
                elif name == "enrich_listings_with_proximity":
                    # Use in-memory listings so the LLM doesn't need to pass them as
                    # arguments, keeping the full listing JSON out of the LLM's output tokens.
                    filter_source = current_listings
                else:
                    filter_source = None
                result = run_tool(
                    name,
                    args,
                    current_listings=filter_source,
                    current_plan_entries=current_plan_entries if name == "modify_viewing_plan" else None,
                    available_slots=available_slots if name == "modify_viewing_plan" else None,
                )
                # Update derived context from tool results so chained tools in same batch see fresh data
                if name == "rental_search":
                    try:
                        data = json.loads(result)
                        if isinstance(data, dict) and isinstance(data.get("listings"), list):
                            # Drop proximity/score master from a previous search so filter/score
                            # use the new results rather than a stale enriched set.
                            enriched_master = []
                            master_listings = data["listings"]
                            current_listings = master_listings
                            display_source = "search"
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "enrich_listings_with_proximity":
                    try:
                        data = json.loads(result)
                        if isinstance(data, dict) and isinstance(data.get("listings"), list):
                            enriched_master = data["listings"]
                            current_listings = enriched_master
                            display_source = "enrich"
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "filter_listings":
                    try:
                        data = json.loads(result)
                        if isinstance(data, dict) and isinstance(data.get("listings"), list):
                            current_listings = data["listings"]
                            display_source = "filter"
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "score_listings_by_preferences":
                    try:
                        data = json.loads(result)
                        if isinstance(data, dict) and isinstance(data.get("listings"), list):
                            scored_list = data["listings"]
                            current_listings = scored_list
                            enriched_master = scored_list
                            display_source = "score"
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name in ("draft_viewing_plan", "modify_viewing_plan"):
                    try:
                        data = json.loads(result)
                        if isinstance(data, dict) and "entries" in data:
                            raw = data.get("entries")
                            if isinstance(raw, list):
                                current_plan_entries = raw
                    except (json.JSONDecodeError, TypeError):
                        pass
                logger.debug("Tool %s completed", name)
                if name == "ask_user":
                    payload = json.loads(result)
                    if payload.get("request_user_input"):
                        listing_state = {
                            "display_list": current_listings or [],
                            "master_list": enriched_master or master_listings or [],
                            "display_source": display_source,
                        }
                        return (
                            messages + [assistant_msg] + tool_results,
                            {
                                "tool_call_id": tc.id,
                                "prompt": payload.get("prompt", ""),
                                "choices": payload.get("choices") or [],
                                "allow_multiple": payload.get("allow_multiple", False),
                            },
                            listing_state,
                        )
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            last_listing_state = {
                "display_list": current_listings or [],
                "master_list": enriched_master or master_listings or [],
                # Carry forward the display_source from a previous iteration when no
                # display-changing tool ran in this batch (e.g. summarize_listings after
                # rental_search), so the streamlit gate still sees the correct source.
                "display_source": display_source if display_source is not None
                                  else (last_listing_state.get("display_source") if last_listing_state else None),
            }
            messages = messages + [assistant_msg] + tool_results
            continue
        # No tool calls: final assistant reply (or enforce draft_viewing_plan after calendar_get_available_slots)
        if _last_completed_tool_name(messages) == "calendar_get_available_slots":
            slots = _get_available_slots_from_messages(messages)
            listings = _get_selected_listings_from_messages(messages)
            if slots and listings:
                logger.debug("Auto-calling draft_viewing_plan (LLM returned no tool calls after calendar_get_available_slots)")
                try:
                    result = run_tool("draft_viewing_plan", {"listings": listings, "available_slots": slots})
                except Exception as e:
                    logger.debug("draft_viewing_plan auto-call failed: %s", e)
                    result = json.dumps({"error": str(e)})
                synthetic_id = f"call_auto_draft_viewing_plan_{uuid.uuid4().hex}"
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": synthetic_id,
                            "type": "function",
                            "function": {
                                "name": "draft_viewing_plan",
                                "arguments": json.dumps({"listings": listings, "available_slots": slots}),
                            },
                        }
                    ],
                }
                tool_results = [{"role": "tool", "tool_call_id": synthetic_id, "content": result}]
                messages = messages + [assistant_msg] + tool_results
                continue
        # Normal final assistant reply: use in-memory state from last tool batch if available
        messages = messages + [{"role": "assistant", "content": msg.content or ""}]
        listing_state = last_listing_state if last_listing_state is not None else _listing_state_from_messages(messages)
        return (messages, None, listing_state)


def run_agent_loop() -> None:
    """Run the chat loop: user message -> LLM -> tool calls -> resolve ask_user in CLI -> loop until reply."""
    project_root = Path(__file__).resolve().parent.parent.parent
    _load_env_file(project_root / ".env")
    client, model = _make_llm_client()
    prefs = _load_preferences_from_file()
    system_content = current_date_context() + flow_instructions() + "\n\n" + _preferences_block(prefs)
    messages: list[dict] = [
        {"role": "system", "content": system_content},
    ]
    print("Property Search Assistant (CLI). Type your search request (e.g. '2 bed rental in Vancouver under 3000' or 'condo for sale in Toronto under 900k'). Empty line to quit.\n")
    while True:
        user_line = (input("You: ").strip() if sys.stdin.isatty() else (sys.stdin.readline() or "").strip())
        if not user_line:
            break
        messages.append({"role": "user", "content": user_line})
        while True:
            messages, payload, _ = run_agent_step(client, model, messages)
            if payload is not None:
                answer_json = prompt_user_for_ask_user(payload)
                messages.append({
                    "role": "tool",
                    "tool_call_id": payload["tool_call_id"],
                    "content": answer_json,
                })
                continue
            if messages and messages[-1].get("role") == "assistant" and messages[-1].get("content"):
                print("\nAssistant:", messages[-1]["content"])
            break
    print("Goodbye.")


def main() -> None:
    run_agent_loop()


if __name__ == "__main__":
    main()
