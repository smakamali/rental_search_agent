"""MCP server: ask_user, rental_search, simulate_viewing_request, calendar tools, draft_viewing_plan. Per spec §5."""

import logging
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from rental_search_agent.adapter import SearchBackendError, search
from rental_search_agent.calendar_service import (
    create_event as do_calendar_create_event,
    delete_event as do_calendar_delete_event,
    get_available_slots as do_calendar_get_available_slots,
    list_events as do_calendar_list_events,
    update_event as do_calendar_update_event,
)
from rental_search_agent.filtering import filter_listings as do_filter_listings
from rental_search_agent.summarizer import summarize_listings as do_summarize_listings
from rental_search_agent.geocoding import (
    geocode_location as do_geocode_location,
    geocode_proximity_references as do_geocode_proximity_references,
)
from rental_search_agent.models import (
    GeocodedReference,
    ListingFilterCriteria,
    ProximityRule,
    RentalSearchFilters,
    RentalSearchResponse,
    SimulateViewingRequestResponse,
    UserDetails,
)
from rental_search_agent.proximity import enrich_listings_with_proximity as do_enrich_listings_with_proximity
from rental_search_agent.proximity_parser import parse_proximity_preferences as do_parse_proximity_preferences
from rental_search_agent.semantic_scoring import score_listings_by_preferences as do_score_listings_by_preferences
from rental_search_agent.viewing_plan import (
    _compute_unused_slots,
    draft_viewing_plan as do_draft_viewing_plan,
    modify_viewing_plan as do_modify_viewing_plan,
)

mcp = FastMCP(
    "Rental Search Assistant",
    json_response=True,
)


@mcp.tool()
def ask_user(
    prompt: str,
    choices: Optional[list[str]] = None,
    allow_multiple: bool = False,
) -> dict[str, Any]:
    """Ask the user for clarification or approval. Single answer (allow_multiple=False) or multi-select (allow_multiple=True). Client must show prompt/choices to user and pass back answer or selected list as tool result."""
    if not prompt or not isinstance(prompt, str):
        raise ValueError("prompt is required and must be a non-empty string.")
    if choices is not None and not isinstance(choices, list):
        raise ValueError("choices must be a list of strings or omitted.")
    return {
        "request_user_input": True,
        "prompt": prompt,
        "choices": choices or [],
        "allow_multiple": allow_multiple,
    }


@mcp.tool()
def rental_search(filters: dict[str, Any]) -> RentalSearchResponse:
    """Run a single logical search for rental listings. Returns listings and total_count. Requires min_bedrooms and location in filters. On backend failure returns an error (never empty list)."""
    try:
        f = RentalSearchFilters.model_validate(filters)
    except Exception as e:
        raise ValueError(f"Invalid filters: {e}") from e
    try:
        return search(f)
    except SearchBackendError as e:
        raise ValueError(str(e)) from e


@mcp.tool()
def filter_listings(
    listings: list[dict[str, Any]],
    filters: dict[str, Any],
    sort_by: Optional[str] = None,
    ascending: bool = True,
    proximity_rules: Optional[list[dict[str, Any]]] = None,
) -> RentalSearchResponse:
    """Narrow and/or sort search results. Pass the current list (e.g. from last rental_search or filter_listings or enrich_listings_with_proximity), filter criteria (optional), optional sort_by and ascending, and optional proximity_rules (from parse_proximity_preferences). When proximity_rules is set, listings must satisfy all rules (AND); unknown proximity keeps the listing. Returns listings and total_count in same shape as rental_search."""
    if not listings or not isinstance(listings, list):
        raise ValueError("listings is required and must be a non-empty list of listing objects.")
    criteria_keys = {"min_bathrooms", "max_bathrooms", "min_bedrooms", "max_bedrooms", "min_sqft", "max_sqft", "rent_min", "rent_max"}
    criteria_dict = {k: v for k, v in (filters or {}).items() if k in criteria_keys and v is not None}
    if not criteria_dict and not sort_by and not (proximity_rules and len(proximity_rules) > 0):
        raise ValueError("At least one filter criterion, sort_by, or proximity_rules is required.")
    try:
        criteria = ListingFilterCriteria.model_validate(criteria_dict) if criteria_dict else ListingFilterCriteria()
    except Exception as e:
        raise ValueError(f"Invalid filter criteria: {e}") from e
    rule_objs = [ProximityRule.model_validate(r) for r in (proximity_rules or [])] if proximity_rules else None
    return do_filter_listings(listings, criteria, sort_by=sort_by, ascending=ascending, proximity_rules=rule_objs)


@mcp.tool()
def summarize_listings(listings: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute statistics (price min/median/mean/max, bedroom distribution, bathroom distribution, size stats, property types) for the current search results. Pass the current list (e.g. from last rental_search or filter_listings). Returns a stats dict for summary."""
    if not listings or not isinstance(listings, list):
        raise ValueError("listings is required and must be a non-empty list of listing objects.")
    return do_summarize_listings(listings)


@mcp.tool()
def parse_proximity_preferences(proximity_text: str) -> dict[str, Any]:
    """Parse free-text proximity preferences (e.g. 'max 30 min drive to downtown, 5 min walk to transit') into a list of structured rules. Returns { rules: [{ location, mode, max_minutes }, ...] }. Use when the user has set proximity_preferences or stated them in chat."""
    if not isinstance(proximity_text, str):
        raise ValueError("proximity_text must be a string.")
    rules = do_parse_proximity_preferences(proximity_text.strip())
    return {"rules": [r.model_dump() for r in rules]}


@mcp.tool()
def geocode_location(location: str) -> dict[str, Any]:
    """Resolve a single location string to coordinates (lat, lon, display_name). Uses Google Geocoding API. Requires GOOGLE_MAPS_API_KEY."""
    if not isinstance(location, str) or not location.strip():
        raise ValueError("location must be a non-empty string.")
    ref = do_geocode_location(location.strip())
    return ref.model_dump()


@mcp.tool()
def geocode_proximity_references(rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Geocode all locations from parsed proximity rules. Skips 'nearest transit station' (resolved at enrichment time). Pass the rules array from parse_proximity_preferences. Returns { refs: [{ location, lat, lon, display_name }, ...] }. Requires GOOGLE_MAPS_API_KEY."""
    if not isinstance(rules, list):
        raise ValueError("rules must be a list of rule objects from parse_proximity_preferences.")
    try:
        rule_objs = [ProximityRule.model_validate(r) for r in rules]
    except Exception as e:
        raise ValueError(f"Invalid rules: {e}") from e
    refs = do_geocode_proximity_references(rule_objs)
    return {"refs": [r.model_dump() for r in refs]}


@mcp.tool()
def enrich_listings_with_proximity(
    listings: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    geocoded_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enrich listings with proximity data (distance_km, duration_min) per rule using Google Directions. Pass current listings, rules from parse_proximity_preferences, and refs from geocode_proximity_references. Returns { listings: [...] } with each listing having a 'proximity' dict. Listings without coordinates get proximity unknown. Requires GOOGLE_MAPS_API_KEY."""
    if not listings or not isinstance(listings, list):
        raise ValueError("listings is required and must be a non-empty list.")
    try:
        rule_objs = [ProximityRule.model_validate(r) for r in (rules or [])]
    except Exception as e:
        raise ValueError(f"Invalid rules: {e}") from e
    try:
        ref_objs = [GeocodedReference.model_validate(r) for r in (geocoded_refs or [])]
    except Exception as e:
        raise ValueError(f"Invalid geocoded_refs: {e}") from e
    enriched = do_enrich_listings_with_proximity(listings, rule_objs, ref_objs)
    return {"listings": enriched, "total_count": len(enriched)}


@mcp.tool()
def score_listings_by_preferences(
    listings: list[dict[str, Any]],
    preferences_text: str,
    query_text: Optional[str] = None,
) -> dict[str, Any]:
    """Score and rank listings by semantic similarity to the user's qualitative preferences. Pass current listings and preferences_text (from stored qualitative_preferences or user message). Returns { listings: [...], total_count } with each listing having semantic_score, sorted by score descending. Call when qualitative_preferences is set and you have search results to rank. Credentials via API_PROVIDER and the corresponding key (OPENROUTER_API_KEY or OPENAI_API_KEY)."""
    if not listings or not isinstance(listings, list):
        raise ValueError("listings is required and must be a non-empty list.")
    if not (preferences_text and isinstance(preferences_text, str) and preferences_text.strip()):
        raise ValueError("preferences_text is required and must be a non-empty string.")
    scored = do_score_listings_by_preferences(
        listings,
        preferences_text.strip(),
        query_text=(query_text or "").strip() or None,
    )
    return {"listings": scored, "total_count": len(scored)}


def do_simulate_viewing_request(
    listing_url: str,
    timeslot: str,
    user_details: dict[str, Any],
) -> SimulateViewingRequestResponse:
    """Shared logic for simulate_viewing_request (used by MCP tool and client)."""
    if not (listing_url and isinstance(listing_url, str) and listing_url.strip()):
        raise ValueError("listing_url is required and must be a non-empty string.")
    if not (timeslot and isinstance(timeslot, str) and timeslot.strip()):
        raise ValueError("timeslot is required and must be a non-empty string.")
    try:
        ud = UserDetails.model_validate(user_details)
    except Exception as e:
        raise ValueError(f"Invalid user_details (name and email required): {e}") from e
    summary = f"Viewing request [simulated] for {listing_url} at {timeslot}. Contact: {ud.name}, {ud.email}."
    contact_url = f"mailto:?subject=Viewing%20request%20for%20listing&body=Requested%20timeslot:%20{timeslot}"
    return SimulateViewingRequestResponse(summary=summary, contact_url=contact_url)


@mcp.tool()
def simulate_viewing_request(
    listing_url: str,
    timeslot: str,
    user_details: dict[str, Any],
) -> SimulateViewingRequestResponse:
    """Simulate a viewing request (no real form POST). Returns a summary and optional contact_url."""
    return do_simulate_viewing_request(listing_url, timeslot, user_details)


def _calendar_error(msg: str) -> None:
    """Re-raise as ValueError for calendar errors."""
    raise ValueError(msg) from None


@mcp.tool()
def calendar_list_events(
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
    max_results: int = 50,
) -> dict[str, Any]:
    """List events in the given time range. time_min and time_max are ISO datetime strings."""
    try:
        events = do_calendar_list_events(time_min, time_max, calendar_id, max_results)
        return {"events": [{"id": e.get("id"), "summary": e.get("summary"), "start": e.get("start"), "end": e.get("end")} for e in events]}
    except Exception as e:
        _calendar_error(str(e))


@mcp.tool()
def calendar_get_available_slots(
    preferred_times: str,
    date_range_start: str,
    date_range_end: str,
    slot_duration_minutes: int = 60,
) -> dict[str, Any]:
    """Get available calendar slots within user's preferred viewing times. Call before drafting a viewing plan."""
    try:
        slots = do_calendar_get_available_slots(
            preferred_times, date_range_start, date_range_end, slot_duration_minutes
        )
        return {"slots": slots}
    except Exception as e:
        logging.warning("calendar_get_available_slots failed: %s", e)
        _calendar_error(str(e))


@mcp.tool()
def calendar_create_event(
    summary: str,
    start_datetime: str,
    end_datetime: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    listing_id: Optional[str] = None,
    listing_url: Optional[str] = None,
) -> dict[str, Any]:
    """Create a calendar event. Store listing_id and listing_url for update flow."""
    try:
        ext = {}
        if listing_id:
            ext["listing_id"] = listing_id
        if listing_url:
            ext["listing_url"] = listing_url
        event = do_calendar_create_event(
            summary, start_datetime, end_datetime,
            description=description, location=location,
            extended_properties=ext if ext else None,
        )
        return {"id": event.get("id"), "htmlLink": event.get("htmlLink"), "summary": event.get("summary")}
    except Exception as e:
        _calendar_error(str(e))


@mcp.tool()
def calendar_update_event(
    event_id: str,
    summary: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
) -> dict[str, Any]:
    """Update an existing calendar event."""
    try:
        event = do_calendar_update_event(
            event_id,
            summary=summary,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            description=description,
            location=location,
        )
        return {"id": event.get("id"), "htmlLink": event.get("htmlLink"), "summary": event.get("summary")}
    except Exception as e:
        _calendar_error(str(e))


@mcp.tool()
def calendar_delete_event(event_id: str) -> dict[str, Any]:
    """Delete a calendar event."""
    try:
        do_calendar_delete_event(event_id)
        return {"deleted": event_id}
    except Exception as e:
        _calendar_error(str(e))


@mcp.tool()
def draft_viewing_plan(listings: list[dict[str, Any]], available_slots: list[dict[str, Any]]) -> dict[str, Any]:
    """Draft a viewing plan: assign slots to listings, clustering nearby listings to minimize commute. Pass selected listings and slots from calendar_get_available_slots."""
    try:
        plan = do_draft_viewing_plan(listings, available_slots)
        unused = _compute_unused_slots(plan.entries, available_slots)
        return {"entries": [e.model_dump() for e in plan.entries], "unused_slots": unused}
    except ValueError as e:
        raise
    except Exception as e:
        raise ValueError(str(e)) from e


@mcp.tool()
def modify_viewing_plan(
    current_entries: list[dict[str, Any]],
    available_slots: list[dict[str, Any]],
    remove: Optional[list[str]] = None,
    add: Optional[list[dict[str, Any]]] = None,
    update: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Modify a viewing plan: add, remove, or update entries. Used when user wants changes in Step 11. Pass current plan entries and available_slots from prior tool results. remove: listing IDs to remove. add: [{listing_id, listing_address, listing_url, slot: {start, end, display}}]. update: [{listing_id, new_slot: {start, end, display}}]."""
    try:
        plan = do_modify_viewing_plan(
            current_entries,
            available_slots,
            remove=remove or [],
            add=add or [],
            update=update or [],
        )
        unused = _compute_unused_slots(plan.entries, available_slots)
        return {"entries": [e.model_dump() for e in plan.entries], "unused_slots": unused}
    except ValueError as e:
        raise
    except Exception as e:
        raise ValueError(str(e)) from e


def main() -> None:
    """Run the MCP server (stdio by default for Cursor/Claude)."""
    mcp.run()


if __name__ == "__main__":
    main()
