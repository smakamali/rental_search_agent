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
from rental_search_agent.models import Listing, ListingFilterCriteria, RentalSearchFilters
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
            "description": "Run a single rental search. Requires min_bedrooms and location in filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "description": "Rental search filters: min_bedrooms (int), location (str) required; optional max_bedrooms, min/max_bathrooms, min/max_sqft, rent_min, rent_max, listing_type. For exact bedroom count (e.g. '2 bed'), set both min_bedrooms and max_bedrooms. For 'at least N', set only min_bedrooms.",
                        "properties": {
                            "min_bedrooms": {"type": "integer", "minimum": 0},
                            "max_bedrooms": {"type": "integer", "minimum": 0},
                            "min_bathrooms": {"type": "integer", "minimum": 0},
                            "max_bathrooms": {"type": "integer", "minimum": 0},
                            "min_sqft": {"type": "integer", "minimum": 0},
                            "max_sqft": {"type": "integer", "minimum": 0},
                            "rent_min": {"type": "number", "minimum": 0},
                            "rent_max": {"type": "number", "minimum": 0},
                            "location": {"type": "string"},
                            "listing_type": {"type": "string", "enum": ["for_rent", "for_sale", "for_sale_or_rent"]},
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
            "description": "Narrow and/or sort the current search results. Call after presenting results when the user asks to filter (e.g. 'only 1 bathroom', 'under 2500') or sort (e.g. 'sort by price', 'cheapest first', 'show most expensive'). Uses the most recent search or filter result as the list. Pass filter criteria and/or sort_by + ascending as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_bathrooms": {"type": "integer", "minimum": 0, "description": "Minimum number of bathrooms."},
                    "max_bathrooms": {"type": "integer", "minimum": 0, "description": "Maximum number of bathrooms."},
                    "min_bedrooms": {"type": "integer", "minimum": 0, "description": "Minimum number of bedrooms."},
                    "max_bedrooms": {"type": "integer", "minimum": 0, "description": "Maximum number of bedrooms."},
                    "min_sqft": {"type": "integer", "minimum": 0, "description": "Minimum square footage."},
                    "max_sqft": {"type": "integer", "minimum": 0, "description": "Maximum square footage."},
                    "rent_min": {"type": "number", "minimum": 0, "description": "Minimum rent (CAD/month)."},
                    "rent_max": {"type": "number", "minimum": 0, "description": "Maximum rent (CAD/month)."},
                    "sort_by": {"type": "string", "enum": ["price", "bedrooms", "bathrooms", "sqft", "address", "id", "title"], "description": "Attribute to sort by (price, bedrooms, bathrooms, sqft, address, id, title). Omit for no sort."},
                    "ascending": {"type": "boolean", "description": "If true, sort ascending (e.g. cheapest first for price). If false, sort descending (e.g. most expensive first). Default true.", "default": True},
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
                    "summary": {"type": "string", "description": "Event title (e.g. Rental viewing: 123 Main St)."},
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
            use_proxy = os.environ.get("USE_PROXY", "").strip().lower() in ("1", "true", "yes")
            resp = search(f, use_proxy=use_proxy)
        except SearchBackendError as e:
            return json.dumps({"error": str(e)})
        return resp.model_dump_json()
    if name == "filter_listings":
        listings = current_listings if current_listings is not None else []
        if not listings:
            return json.dumps({"error": "No current search results to filter or sort. Run a search first."})
        sort_by = arguments.get("sort_by")
        ascending = arguments.get("ascending", True)
        criteria_keys = {"min_bathrooms", "max_bathrooms", "min_bedrooms", "max_bedrooms", "min_sqft", "max_sqft", "rent_min", "rent_max"}
        criteria_dict = {k: v for k, v in arguments.items() if k in criteria_keys and v is not None}
        if not criteria_dict and not sort_by:
            return json.dumps({"error": "At least one filter criterion or sort_by is required."})
        criteria = ListingFilterCriteria.model_validate(criteria_dict) if criteria_dict else ListingFilterCriteria()
        resp = do_filter_listings(listings, criteria, sort_by=sort_by, ascending=ascending)
        return resp.model_dump_json()
    if name == "summarize_listings":
        listings = current_listings if current_listings is not None else []
        if not listings:
            return json.dumps({"error": "No current search results to summarize. Run a search first."})
        result = do_summarize_listings(listings)
        return json.dumps(result)
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
                summary=arguments.get("summary") or "Rental viewing",
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


# OpenRouter: unified API for 400+ models (https://openrouter.ai/docs)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


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
    """Build LLM client and model name. Prefer OpenRouter if OPENROUTER_API_KEY is set."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openrouter_key:
        model = os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        client = OpenAI(
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/smakamali/rental_search_agent",
                "X-Title": "Rental Search Assistant",
            },
        )
        return client, model
    if openai_key:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        client = OpenAI(api_key=openai_key)
        return client, model
    print(
        "Set OPENROUTER_API_KEY (recommended, see https://openrouter.ai) or OPENAI_API_KEY to run the client.",
        file=sys.stderr,
    )
    sys.exit(1)


logger = logging.getLogger(__name__)


def run_agent_step(client: OpenAI, model: str, messages: list[dict]) -> tuple[list[dict], dict | None]:
    """Run one or more LLM calls and tool executions. Returns (updated_messages, ask_user_payload | None).
    When ask_user needs input, returns (messages + assistant_msg + tool_results_before_ask, payload) with
    payload containing tool_call_id, prompt, choices, allow_multiple so the caller can append the user's answer."""
    while True:
        logger.debug("Calling LLM (model=%s)...", model)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        logger.debug("LLM responded")
        if not msg:
            return (messages, None)
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
            current_plan_entries = _get_viewing_plan_from_messages(messages)
            available_slots = _get_available_slots_from_messages(messages)
            for tc in msg.tool_calls:
                name = tc.function.name
                logger.debug("Executing tool: %s", name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = run_tool(
                    name,
                    args,
                    current_listings=current_listings if name in ("filter_listings", "summarize_listings") else None,
                    current_plan_entries=current_plan_entries if name == "modify_viewing_plan" else None,
                    available_slots=available_slots if name == "modify_viewing_plan" else None,
                )
                # Update derived context from tool results so chained tools in same batch see fresh data
                if name in ("rental_search", "filter_listings"):
                    try:
                        data = json.loads(result)
                        if isinstance(data, dict) and "listings" in data:
                            raw = data.get("listings")
                            if isinstance(raw, list):
                                current_listings = raw
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
                        return (
                            messages + [assistant_msg] + tool_results,
                            {
                                "tool_call_id": tc.id,
                                "prompt": payload.get("prompt", ""),
                                "choices": payload.get("choices") or [],
                                "allow_multiple": payload.get("allow_multiple", False),
                            },
                        )
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})
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
        # Normal final assistant reply
        messages = messages + [{"role": "assistant", "content": msg.content or ""}]
        return (messages, None)


def run_agent_loop() -> None:
    """Run the chat loop: user message -> LLM -> tool calls -> resolve ask_user in CLI -> loop until reply."""
    project_root = Path(__file__).resolve().parent.parent.parent
    _load_env_file(project_root / ".env")
    client, model = _make_llm_client()
    messages: list[dict] = [
        {"role": "system", "content": current_date_context() + flow_instructions()},
    ]
    print("Rental Search Assistant (CLI). Type your search request (e.g. '2 bed in Vancouver under 3000'). Empty line to quit.\n")
    while True:
        user_line = (input("You: ").strip() if sys.stdin.isatty() else (sys.stdin.readline() or "").strip())
        if not user_line:
            break
        messages.append({"role": "user", "content": user_line})
        while True:
            messages, payload = run_agent_step(client, model, messages)
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
