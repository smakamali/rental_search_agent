"""Streamlit chat UI for the rental search agent. Uses the MCP server for tool calls (same structure as streamlit_app.py).

Transport: By default the client spawns the MCP server as a stdio subprocess. On Windows this can fail (server
exits during handshake). Set env MCP_USE_HTTP=1 to run the server in-process over HTTP (port 8000, path /mcp) instead.
"""

import asyncio
import html
import json
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

try:
    import folium
except ImportError:
    folium = None
try:
    import pydeck as pdk
except ImportError:
    pdk = None

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# Optional: use streamable HTTP instead of stdio (avoids subprocess/stdio issues on Windows)
_MCP_USE_HTTP = os.environ.get("MCP_USE_HTTP", "").strip().lower() in ("1", "true", "yes")
_HTTP_SERVER_STARTED = False
_HTTP_SERVER_LOCK = threading.Lock()

if _MCP_USE_HTTP:
    from mcp.client.streamable_http import streamable_http_client


def _ensure_http_server() -> None:
    """Start MCP server with streamable-http in a daemon thread (port 8000, path /mcp)."""
    global _HTTP_SERVER_STARTED
    with _HTTP_SERVER_LOCK:
        if _HTTP_SERVER_STARTED:
            return
        from rental_search_agent.server import mcp

        def run() -> None:
            mcp.run(transport="streamable-http")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        _HTTP_SERVER_STARTED = True
    time.sleep(1.5)

from rental_search_agent.agent import current_date_context, flow_instructions
from rental_search_agent.api_config import has_api_credentials
from rental_search_agent.calendar_service import default_timezone
from rental_search_agent.chat_summary import summarize_conversation_for_preferences
from rental_search_agent.listing_analysis import analyze_listing_against_preferences
from rental_search_agent.client import (
    TOOLS,
    _get_available_slots_from_messages,
    _get_current_listings_from_messages,
    _get_selected_listings_from_messages,
    _get_viewing_plan_from_messages,
    _last_completed_tool_name,
    _load_env_file,
    _make_llm_client,
)

# Keys for stored user preferences (viewing time, name, email, phone, proximity, listing preferences)
PREF_KEYS = ("viewing_preference", "name", "email", "phone", "proximity_preferences", "qualitative_preferences")

_CRITERIA_KEYS = {
    "min_bathrooms",
    "max_bathrooms",
    "min_bedrooms",
    "max_bedrooms",
    "min_sqft",
    "max_sqft",
    "rent_min",
    "rent_max",
}


def _preferences_file() -> Path:
    """Path to optional JSON file for persisting preferences across sessions."""
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


def _save_preferences_to_file(prefs: dict) -> None:
    """Write preferences to file. No-op on failure (e.g. directory missing)."""
    path = _preferences_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: prefs.get(k, "") for k in PREF_KEYS}, indent=2))
    except Exception:
        pass


def _preferences_block(prefs: dict) -> str:
    """Build the preferences block to inject into the system message."""
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


def _build_system_content() -> str:
    """System message content: current date + flow instructions + current preferences block."""
    prefs = st.session_state.get("user_preferences") or {k: "" for k in PREF_KEYS}
    return current_date_context() + flow_instructions() + "\n\n" + _preferences_block(prefs)


def _ensure_env_loaded() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    _load_env_file(project_root / ".env")


def _get_client_and_model():
    """Return (client, model), cached in session state. Ensures env is loaded first."""
    if "llm_client" in st.session_state and "llm_model" in st.session_state:
        return st.session_state["llm_client"], st.session_state["llm_model"]
    _ensure_env_loaded()
    if not has_api_credentials():
        return None, None
    client, model = _make_llm_client()
    st.session_state["llm_client"] = client
    st.session_state["llm_model"] = model
    return client, model


def _init_session_state() -> None:
    _ensure_env_loaded()
    if "user_preferences" not in st.session_state:
        st.session_state["user_preferences"] = _load_preferences_from_file()
    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role": "system", "content": _build_system_content()},
        ]
    else:
        # Keep system message in sync with current preferences
        st.session_state["messages"][0] = {"role": "system", "content": _build_system_content()}
    if "pending_ask" not in st.session_state:
        st.session_state["pending_ask"] = None
    if "analyze_listing_id" not in st.session_state:
        st.session_state["analyze_listing_id"] = None
    if "analyze_listing" not in st.session_state:
        st.session_state["analyze_listing"] = None
    if "analysis_result" not in st.session_state:
        st.session_state["analysis_result"] = {}
    if "chat_summary" not in st.session_state:
        st.session_state["chat_summary"] = ""
    if "chat_summary_message_count" not in st.session_state:
        st.session_state["chat_summary_message_count"] = None


# ---- MCP tool argument mapping and result extraction ----

def _build_mcp_tool_args(
    name: str,
    llm_args: dict,
    current_listings: list[dict],
    current_plan_entries: list[dict],
    available_slots: list[dict],
) -> dict:
    """Build the argument dict for session.call_tool(name, arguments) from LLM args and message-derived context."""
    if name == "ask_user":
        return {
            "prompt": llm_args.get("prompt", ""),
            "choices": llm_args.get("choices") or [],
            "allow_multiple": llm_args.get("allow_multiple", False),
        }
    if name == "rental_search":
        return {"filters": llm_args.get("filters", {})}
    if name == "filter_listings":
        criteria_dict = {k: llm_args[k] for k in _CRITERIA_KEYS if k in llm_args and llm_args[k] is not None}
        return {
            "listings": current_listings,
            "filters": criteria_dict,
            "sort_by": llm_args.get("sort_by"),
            "ascending": llm_args.get("ascending", True),
        }
    if name == "summarize_listings":
        return {"listings": current_listings}
    if name == "simulate_viewing_request":
        return {
            "listing_url": llm_args.get("listing_url", ""),
            "timeslot": llm_args.get("timeslot", ""),
            "user_details": llm_args.get("user_details", {}),
        }
    if name == "calendar_get_available_slots":
        tz = ZoneInfo(default_timezone())
        now = datetime.now(tz)
        tomorrow = (now.date() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        two_weeks = (now.date() + timedelta(days=14)).strftime("%Y-%m-%dT23:59:59")
        date_range_start = (llm_args.get("date_range_start") or "").strip() or tomorrow
        date_range_end = (llm_args.get("date_range_end") or "").strip() or two_weeks
        return {
            "preferred_times": llm_args.get("preferred_times", ""),
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "slot_duration_minutes": llm_args.get("slot_duration_minutes", 60),
        }
    if name == "draft_viewing_plan":
        return {
            "listings": llm_args.get("listings", []),
            "available_slots": llm_args.get("available_slots", []),
        }
    if name == "modify_viewing_plan":
        return {
            "current_entries": current_plan_entries,
            "available_slots": available_slots,
            "remove": llm_args.get("remove") or [],
            "add": llm_args.get("add") or [],
            "update": llm_args.get("update") or [],
        }
    if name == "calendar_create_event":
        return {
            "summary": llm_args.get("summary") or "Rental viewing",
            "start_datetime": llm_args.get("start_datetime", ""),
            "end_datetime": llm_args.get("end_datetime", ""),
            "description": llm_args.get("description"),
            "location": llm_args.get("location"),
            "listing_id": llm_args.get("listing_id"),
            "listing_url": llm_args.get("listing_url"),
        }
    if name == "calendar_update_event":
        return {
            "event_id": llm_args.get("event_id", ""),
            "summary": llm_args.get("summary"),
            "start_datetime": llm_args.get("start_datetime"),
            "end_datetime": llm_args.get("end_datetime"),
            "description": llm_args.get("description"),
            "location": llm_args.get("location"),
        }
    if name == "calendar_delete_event":
        return {"event_id": llm_args.get("event_id", "")}
    if name == "calendar_list_events":
        return {
            "time_min": llm_args.get("time_min", ""),
            "time_max": llm_args.get("time_max", ""),
            "calendar_id": llm_args.get("calendar_id", "primary"),
            "max_results": llm_args.get("max_results", 50),
        }
    if name == "analyze_listing_preferences":
        return {
            "listing": llm_args.get("listing", {}),
            "preferences_text": llm_args.get("preferences_text", ""),
        }
    return llm_args


def _call_tool_result_to_content(result) -> str:
    """Convert MCP CallToolResult to JSON string for tool message content."""
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        parts = []
        if result.content:
            for block in result.content:
                if getattr(block, "text", None):
                    parts.append(block.text)
        msg = " ".join(parts).strip() if parts else "Tool error"
        return json.dumps({"error": msg})
    if getattr(result, "structured_content", None) is not None:
        return json.dumps(result.structured_content)
    if result.content:
        for block in result.content:
            if getattr(block, "text", None):
                return block.text
    return json.dumps({})


_log = logging.getLogger(__name__)

# Ensure MCP step logs go to the debug file (worker thread may run before main thread sets up client logging).
_DEBUG_FILE_HANDLER_ADDED = False


def _ensure_mcp_debug_log() -> None:
    """Ensure rental_search_agent_debug.log has a handler so [MCP] logs are written (e.g. from worker thread)."""
    global _DEBUG_FILE_HANDLER_ADDED
    if _DEBUG_FILE_HANDLER_ADDED:
        return
    project_root = Path(__file__).resolve().parent.parent.parent
    log_file = project_root / "rental_search_agent_debug.log"
    # Add handler to our module logger so logs definitely go to the file (don't rely on propagation).
    for h in _log.handlers:
        if isinstance(h, logging.FileHandler) and "rental_search_agent_debug.log" in (
            getattr(h, "baseFilename", "") or ""
        ):
            _DEBUG_FILE_HANDLER_ADDED = True
            return
    handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.DEBUG)
    _DEBUG_FILE_HANDLER_ADDED = True
    # Append one line so we know the file path and that writes work (handles Streamlit subprocess cwd).
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n# MCP debug log file: {log_file.resolve()}\n")
    except Exception:
        pass


async def _run_session_loop(
    session: ClientSession,
    client,
    model: str,
    messages: list[dict],
    t0: float,
) -> tuple[list[dict], dict | None]:
    """Initialize session and run the LLM + tool loop. Used by both stdio and HTTP transports."""
    _log.info("[MCP] Initializing session... (%.1fs)", time.monotonic() - t0)
    await session.initialize()
    _log.info("[MCP] Session initialized (%.1fs)", time.monotonic() - t0)

    while True:
        _log.info("[MCP] Calling LLM... (%.1fs)", time.monotonic() - t0)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        _log.info("[MCP] LLM responded (%.1fs)", time.monotonic() - t0)
        if not msg:
            return (messages, None)
        if msg.tool_calls:
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
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                mcp_args = _build_mcp_tool_args(
                    name,
                    args,
                    current_listings=current_listings,
                    current_plan_entries=current_plan_entries,
                    available_slots=available_slots,
                )
                t_tool = time.monotonic()
                _log.info("[MCP] Calling tool %s (%.1fs)", name, t_tool - t0)
                try:
                    result = await session.call_tool(name, mcp_args)
                except Exception as e:
                    result_str = json.dumps({"error": str(e)})
                    _log.warning("[MCP] Tool %s failed: %s (%.1fs)", name, e, time.monotonic() - t0)
                else:
                    result_str = _call_tool_result_to_content(result)
                    _log.info("[MCP] Tool %s done (%.1fs)", name, time.monotonic() - t_tool)
                if name in ("rental_search", "filter_listings"):
                    try:
                        data = json.loads(result_str)
                        if isinstance(data, dict) and "listings" in data:
                            raw = data.get("listings")
                            if isinstance(raw, list):
                                current_listings = raw
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name in ("draft_viewing_plan", "modify_viewing_plan"):
                    try:
                        data = json.loads(result_str)
                        if isinstance(data, dict) and "entries" in data:
                            raw = data.get("entries")
                            if isinstance(raw, list):
                                current_plan_entries = raw
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name == "ask_user":
                    try:
                        payload = json.loads(result_str)
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
                    except (json.JSONDecodeError, TypeError):
                        pass
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
            messages = messages + [assistant_msg] + tool_results
            continue
        # No tool calls: maybe auto draft_viewing_plan, or final reply
        if _last_completed_tool_name(messages) == "calendar_get_available_slots":
            slots = _get_available_slots_from_messages(messages)
            listings = _get_selected_listings_from_messages(messages)
            if slots and listings:
                mcp_args = {"listings": listings, "available_slots": slots}
                try:
                    result = await session.call_tool("draft_viewing_plan", mcp_args)
                    result_str = _call_tool_result_to_content(result)
                except Exception as e:
                    result_str = json.dumps({"error": str(e)})
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
                                "arguments": json.dumps(mcp_args),
                            },
                        }
                    ],
                }
                tool_results = [{"role": "tool", "tool_call_id": synthetic_id, "content": result_str}]
                messages = messages + [assistant_msg] + tool_results
                continue
        messages = messages + [{"role": "assistant", "content": msg.content or ""}]
        return (messages, None)


async def _run_agent_step_mcp(client, model: str, messages: list[dict]) -> tuple[list[dict], dict | None]:
    """Run one or more LLM calls and MCP tool executions. Same contract as client.run_agent_step."""
    _ensure_mcp_debug_log()
    t0 = time.monotonic()
    project_root = Path(__file__).resolve().parent.parent.parent

    if _MCP_USE_HTTP:
        _ensure_http_server()
        _log.info("[MCP] Connecting via streamable HTTP (%.1fs)", time.monotonic() - t0)
        async with streamable_http_client("http://127.0.0.1:8000/mcp") as (read_stream, write_stream, _):
            session = ClientSession(read_stream, write_stream)
            return await _run_session_loop(session, client, model, messages, t0)
    else:
        server_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-u", "-m", "rental_search_agent.server"],
            cwd=project_root,
            env=server_env,
        )
        _log.info("[MCP] Spawning server subprocess (%.1fs)", time.monotonic() - t0)
        server_stderr_path = project_root / "mcp_server_stderr.log"
        errlog = open(server_stderr_path, "a", encoding="utf-8")
        try:
            errlog.write(f"\n--- MCP server stderr (session at {time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
            errlog.flush()
        except Exception:
            pass
        try:
            async with stdio_client(server_params, errlog=errlog) as (read_stream, write_stream):
                _log.info("[MCP] Server connected (%.1fs)", time.monotonic() - t0)
                session = ClientSession(read_stream, write_stream)
                return await _run_session_loop(session, client, model, messages, t0)
        finally:
            try:
                errlog.close()
            except Exception:
                pass


def run_agent_step_mcp_sync(client, model: str, messages: list[dict]) -> tuple[list[dict], dict | None]:
    """Sync wrapper: run the MCP-backed agent step in a dedicated thread (fresh event loop, avoids Streamlit loop conflicts)."""
    return asyncio.run(_run_agent_step_mcp(client, model, messages))


# One worker so we don't spawn many MCP server processes at once; reused across requests.
_executor = ThreadPoolExecutor(max_workers=1)
_MCP_STEP_TIMEOUT_SECONDS = 120


def run_agent_step_mcp_sync_from_streamlit(
    client, model: str, messages: list[dict]
) -> tuple[list[dict], dict | None]:
    """Run MCP agent step in a thread with timeout; for use from Streamlit to avoid blocking the UI loop."""
    future = _executor.submit(run_agent_step_mcp_sync, client, model, messages)
    try:
        return future.result(timeout=_MCP_STEP_TIMEOUT_SECONDS)
    except Exception as e:
        logging.getLogger(__name__).exception("MCP agent step failed")
        raise


# ---- UI (same structure as streamlit_app.py) ----

def _get_latest_search_listings(messages: list[dict]) -> list[dict]:
    """Extract the most recent rental_search or filter_listings result from message history."""
    listings = []
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
                listings = raw
            break
    return listings


def _listings_to_table_rows(listings: list[dict]) -> list[dict]:
    """Build table-friendly rows: rank, MLS id, address, bed, bath, size, rent, URL."""
    rows = []
    for i, listing in enumerate(listings):
        bath = listing.get("bathrooms")
        sqft = listing.get("sqft")
        rent = listing.get("price_display") or (
            f"${int(listing.get('price', 0)):,}" if listing.get("price") is not None else "—"
        )
        rows.append({
            "rank": i + 1,
            "MLS id": listing.get("id") or "—",
            "address": listing.get("address") or "—",
            "bed": listing.get("bedrooms") if listing.get("bedrooms") is not None else "—",
            "bath": f"{float(bath):g}" if bath is not None else "—",
            "size": str(int(sqft)) if sqft is not None else "—",
            "rent": rent,
            "URL": listing.get("url") or "",
        })
    return rows


def _render_results_table(listings: list[dict]) -> None:
    """Render search results as custom rows with an Analyze button per listing."""
    if not listings:
        return
    # Header row: Rank, MLS id (link), Address, Bed, Bath, Size, Rent, Analyze
    header_cols = st.columns([0.5, 1, 2, 0.5, 0.5, 0.6, 0.8, 1])
    with header_cols[0]:
        st.caption("Rank")
    with header_cols[1]:
        st.caption("MLS id")
    with header_cols[2]:
        st.caption("Address")
    with header_cols[3]:
        st.caption("Bed")
    with header_cols[4]:
        st.caption("Bath")
    with header_cols[5]:
        st.caption("Size")
    with header_cols[6]:
        st.caption("Rent")
    with header_cols[7]:
        st.caption("Analyze")
    st.divider()
    for i, listing in enumerate(listings):
        bath = listing.get("bathrooms")
        sqft = listing.get("sqft")
        rent = listing.get("price_display") or (
            f"${int(listing.get('price', 0)):,}" if listing.get("price") is not None else "—"
        )
        url = listing.get("url") or ""
        mls_id = listing.get("id") or "—"
        row_cols = st.columns([0.5, 1, 2, 0.5, 0.5, 0.6, 0.8, 1])
        with row_cols[0]:
            st.write(i + 1)
        with row_cols[1]:
            if url:
                st.link_button(mls_id, url)
            else:
                st.write(mls_id)
        with row_cols[2]:
            st.write(listing.get("address") or "—")
        with row_cols[3]:
            st.write(listing.get("bedrooms") if listing.get("bedrooms") is not None else "—")
        with row_cols[4]:
            st.write(f"{float(bath):g}" if bath is not None else "—")
        with row_cols[5]:
            st.write(str(int(sqft)) if sqft is not None else "—")
        with row_cols[6]:
            st.write(rent)
        with row_cols[7]:
            if st.button("Analyze", key=f"analyze_{listing.get('id', i)}"):
                st.session_state["analyze_listing_id"] = listing.get("id")
                st.session_state["analyze_listing"] = listing
                st.rerun()


def _listings_cache_key(listings: list[dict]) -> str:
    """Stable JSON string for listings, used as cache key."""
    return json.dumps(listings, sort_keys=True, default=str)


@st.cache_data(show_spinner=False)
def _get_map_html_cached(listings_json: str) -> str | None:
    """Build Folium map HTML from listings. Cached by listings content."""
    if folium is None:
        return None
    listings = json.loads(listings_json) if listings_json else []
    map_points, center_lat, center_lon = _build_map_data(listings)
    if not map_points or center_lat is None or center_lon is None:
        return None
    m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
    for pt in map_points:
        label = pt["label"]
        url = pt.get("url") or "#"
        url_escaped = html.escape(url)
        folium.Marker(
            location=[pt["lat"], pt["lon"]],
            icon=folium.DivIcon(
                icon_size=(32, 32),
                icon_anchor=(16, 16),
                html=(
                    '<div style="font-size:14pt;font-weight:bold;color:white;text-align:center;'
                    'line-height:30px;width:30px;height:30px;border-radius:50%;'
                    'background-color:#4682B4;border:2px solid white;">'
                    f'<a href="{url_escaped}" target="_blank" rel="noopener" '
                    'style="color:white;text-decoration:none;">{}</a>'
                ).format(label),
            ),
        ).add_to(m)
    return m._repr_html_()


def _build_map_data(listings: list[dict]) -> tuple[list[dict], float | None, float | None]:
    """Build list of {lat, lon, label, url} for listings with valid coordinates."""
    points = []
    lats, lons = [], []
    for i, listing in enumerate(listings):
        lat = listing.get("latitude")
        lon = listing.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        url = listing.get("url") or ""
        points.append({"lat": lat, "lon": lon, "label": str(i + 1), "url": url})
        lats.append(lat)
        lons.append(lon)
    if not points:
        return points, None, None
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    return points, center_lat, center_lon


def _render_results_map(map_points: list[dict], center_lat: float, center_lon: float) -> None:
    """Render a map with points labeled by listing order."""
    if folium is not None:
        m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
        for pt in map_points:
            label = pt["label"]
            url = pt.get("url") or "#"
            url_escaped = html.escape(url)
            folium.Marker(
                location=[pt["lat"], pt["lon"]],
                icon=folium.DivIcon(
                    icon_size=(32, 32),
                    icon_anchor=(16, 16),
                    html=(
                        '<div style="font-size:14pt;font-weight:bold;color:white;text-align:center;'
                        'line-height:30px;width:30px;height:30px;border-radius:50%;'
                        'background-color:#4682B4;border:2px solid white;">'
                        f'<a href="{url_escaped}" target="_blank" rel="noopener" '
                        'style="color:white;text-decoration:none;">{}</a>'
                    ).format(label),
                ),
            ).add_to(m)
        st.components.v1.html(m._repr_html_(), height=400, scrolling=False)
        return
    if pdk is not None:
        scatter = pdk.Layer(
            "ScatterplotLayer",
            data=map_points,
            get_position="[lon, lat]",
            get_radius=200,
            get_fill_color=[70, 130, 180],
            radius_min_pixels=6,
            radius_max_pixels=12,
        )
        text = pdk.Layer(
            "TextLayer",
            data=map_points,
            get_position="[lon, lat]",
            get_text="label",
            get_size=14,
            get_color=[255, 255, 255],
            get_text_anchor="middle",
            get_alignment_baseline="center",
        )
        view_state = pdk.ViewState(
            latitude=center_lat,
            longitude=center_lon,
            zoom=11,
            pitch=0,
        )
        st.pydeck_chart(
            pdk.Deck(
                layers=[scatter, text],
                initial_view_state=view_state,
            ),
            width='stretch',
            height=400,
        )
        return
    st.caption("Map unavailable: install folium (recommended) or pydeck to show results on a map.")


def _render_chat_history() -> None:
    """Render user and assistant messages (skip system and tool)."""
    for msg in st.session_state["messages"]:
        role = msg.get("role")
        if role == "system" or role == "tool":
            continue
        if role == "user":
            with st.chat_message("user"):
                st.markdown(msg.get("content", ""))
        elif role == "assistant":
            content = msg.get("content", "")
            if content:
                with st.chat_message("assistant"):
                    st.markdown(content)


def _build_answer_json(pending: dict, answer_value: str | list[str]) -> str:
    """Build JSON string for tool result: { answer } or { selected }."""
    if pending.get("allow_multiple"):
        selected = answer_value if isinstance(answer_value, list) else [answer_value] if answer_value else []
        return json.dumps({"selected": selected})
    return json.dumps({"answer": answer_value if isinstance(answer_value, str) else str(answer_value or "")})


def _render_preferences_sidebar() -> None:
    """Sidebar form to set or edit viewing time, name, email, phone."""
    prefs = st.session_state.get("user_preferences") or {k: "" for k in PREF_KEYS}
    with st.sidebar:
        st.subheader("Your details")
        st.caption("Optional. If set, the assistant will use these and not ask again.")
        with st.form("preferences_form"):
            viewing = st.text_input(
                "Preferred viewing times",
                value=prefs.get("viewing_preference", ""),
                placeholder="e.g. weekday evenings 6–8pm",
                key="pref_viewing",
            )
            name = st.text_input("Name", value=prefs.get("name", ""), key="pref_name")
            email = st.text_input("Email", value=prefs.get("email", ""), key="pref_email")
            phone = st.text_input("Phone (optional)", value=prefs.get("phone", ""), key="pref_phone")
            proximity = st.text_area(
                "Proximity preferences",
                value=prefs.get("proximity_preferences", ""),
                placeholder="e.g. max 30 min drive to downtown, 5 min walk to transit",
                key="pref_proximity",
            )
            qualitative = st.text_area(
                "Listing preferences",
                value=prefs.get("qualitative_preferences", ""),
                placeholder="e.g. balcony, parking, gym, pet-friendly",
                key="pref_qualitative",
            )
            submitted = st.form_submit_button("Save")
            if submitted:
                new_prefs = {
                    "viewing_preference": (viewing or "").strip(),
                    "name": (name or "").strip(),
                    "email": (email or "").strip(),
                    "phone": (phone or "").strip(),
                    "proximity_preferences": (proximity or "").strip(),
                    "qualitative_preferences": (qualitative or "").strip(),
                }
                st.session_state["user_preferences"] = new_prefs
                _save_preferences_to_file(new_prefs)
                st.session_state["messages"][0] = {"role": "system", "content": _build_system_content()}
                st.rerun()


def _render_ask_form(pending: dict) -> None:
    """Show form for ask_user: prompt + input/selectbox/multiselect. On submit, append tool result and run step."""
    st.markdown(f"**{pending['prompt']}**")
    choices = pending.get("choices") or []
    allow_multiple = pending.get("allow_multiple", False)

    with st.form("ask_user_form", clear_on_submit=True):
        if choices:
            if allow_multiple:
                selected = st.multiselect("Select one or more", choices, key="ask_multiselect")
                submit_val = selected
            else:
                selected = st.selectbox("Choose one", [""] + choices, key="ask_selectbox")
                submit_val = selected if selected else None
        else:
            submit_val = st.text_input("Your answer", key="ask_text")

        submitted = st.form_submit_button("Submit")
        if submitted:
            if allow_multiple and not isinstance(submit_val, list):
                submit_val = [submit_val] if submit_val else []
            answer_json = _build_answer_json(pending, submit_val)
            messages = st.session_state["messages"]
            messages.append({
                "role": "tool",
                "tool_call_id": pending["tool_call_id"],
                "content": answer_json,
            })
            st.session_state["messages"] = messages
            st.session_state["pending_ask"] = None

            client, model = _get_client_and_model()
            if client is None or model is None:
                st.error("Set API_PROVIDER (openrouter or openai) and the corresponding API key (OPENROUTER_API_KEY or OPENAI_API_KEY) in .env.")
                st.stop()
            while True:
                try:
                    with st.spinner("Calling assistant..."):
                        messages, payload = run_agent_step_mcp_sync_from_streamlit(
                            client, model, st.session_state["messages"]
                        )
                except FuturesTimeoutError:
                    st.error("The assistant took too long to respond. Please try again.")
                    st.stop()
                except Exception as e:
                    st.error(f"Assistant error: {e}")
                    st.stop()
                st.session_state["messages"] = messages
                if payload is not None:
                    st.session_state["pending_ask"] = payload
                    st.rerun()
                break
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Rental Search Assistant (MCP)", page_icon="🏠", layout="wide")
    st.title("Rental Search Assistant (MCP)")

    _ensure_env_loaded()
    _init_session_state()
    _render_preferences_sidebar()

    client, model = _get_client_and_model()
    if client is None or model is None:
        st.error("Set API_PROVIDER (openrouter or openai) and the corresponding API key (OPENROUTER_API_KEY or OPENAI_API_KEY) in .env to run the assistant.")
        st.stop()

    col_content, col_chat = st.columns([2, 1], vertical_alignment="bottom")

    with col_content:
        listings = _get_latest_search_listings(st.session_state["messages"])
        if listings:
            with st.expander("Search results table", expanded=True):
                _render_results_table(listings)
            # Analysis card: when user clicked Analyze, run analysis and show result
            analyze_listing_id = st.session_state.get("analyze_listing_id")
            analyze_listing = st.session_state.get("analyze_listing")
            analysis_result = st.session_state.get("analysis_result", {})
            if analyze_listing_id and analyze_listing:
                prefs = st.session_state.get("user_preferences") or {}
                qualitative = (prefs.get("qualitative_preferences") or "").strip()
                proximity = (prefs.get("proximity_preferences") or "").strip()
                preferences_text = qualitative
                if proximity:
                    preferences_text = (
                        f"{preferences_text}\n\nProximity: {proximity}".strip()
                        if preferences_text
                        else f"Proximity: {proximity}"
                    )
                if not preferences_text:
                    with st.expander("Analysis result", expanded=True):
                        st.warning("Set listing or proximity preferences in the sidebar first, then click Analyze again.")
                        if st.button("Clear analysis"):
                            st.session_state["analyze_listing_id"] = None
                            st.session_state["analyze_listing"] = None
                            st.rerun()
                else:
                    messages = st.session_state["messages"]
                    current_count = len(messages)
                    if st.session_state.get("chat_summary_message_count") != current_count:
                        with st.spinner("Summarizing conversation..."):
                            summary = summarize_conversation_for_preferences(messages)
                            st.session_state["chat_summary"] = summary or ""
                            st.session_state["chat_summary_message_count"] = current_count
                            st.session_state["analysis_result"] = {}
                        st.rerun()
                    conversation_context = st.session_state.get("chat_summary") or ""
                    if analyze_listing_id not in analysis_result:
                        with st.spinner("Analyzing listing..."):
                            try:
                                result = analyze_listing_against_preferences(
                                    analyze_listing,
                                    preferences_text,
                                    conversation_context=conversation_context or None,
                                )
                                st.session_state.setdefault("analysis_result", {})[
                                    analyze_listing_id
                                ] = result
                            except Exception as e:
                                st.session_state.setdefault("analysis_result", {})[
                                    analyze_listing_id
                                ] = {"error": str(e)}
                        st.rerun()
                    result = st.session_state["analysis_result"].get(analyze_listing_id)
                    if result and isinstance(result, dict):
                        if "error" in result:
                            with st.expander("Analysis result", expanded=True):
                                st.error(result["error"])
                                if st.button("Clear analysis"):
                                    st.session_state["analyze_listing_id"] = None
                                    st.session_state["analyze_listing"] = None
                                    st.session_state["analysis_result"] = {}
                                    st.rerun()
                        else:
                            addr = analyze_listing.get("address") or analyze_listing.get("id") or "Listing"
                            with st.expander(f"Analysis: {addr}", expanded=True):
                                st.metric("Match score", f"{result.get('match_score_pct', 0)}%")
                                col_matches, col_gaps = st.columns(2)
                                with col_matches:
                                    st.subheader("Key matches")
                                    for m in result.get("key_matches") or []:
                                        st.markdown(f"- {m}")
                                with col_gaps:
                                    st.subheader("Key gaps")
                                    for g in result.get("key_gaps") or []:
                                        st.markdown(f"- {g}")
                                if st.button("Clear analysis"):
                                    st.session_state["analyze_listing_id"] = None
                                    st.session_state["analyze_listing"] = None
                                    st.rerun()
            map_points, center_lat, center_lon = _build_map_data(listings)
            if map_points and center_lat is not None and center_lon is not None:
                with st.expander("Search results map", expanded=True):
                    if folium is not None:
                        map_html = _get_map_html_cached(_listings_cache_key(listings))
                        if map_html:
                            st.components.v1.html(map_html, height=400, scrolling=False)
                        else:
                            _render_results_map(map_points, center_lat, center_lon)
                    else:
                        _render_results_map(map_points, center_lat, center_lon)
            elif not map_points:
                with st.expander("Search results map", expanded=False):
                    st.caption("No map: addresses have no coordinates.")
        else:
            st.caption("Run a search to see results here.")

    with col_chat:
        st.subheader("Chat")
        _render_chat_history()
        pending = st.session_state.get("pending_ask")
        if pending is not None:
            with st.chat_message("assistant"):
                _render_ask_form(pending)
            return
        if prompt := st.chat_input("Type your search request (e.g. 2 bed in Vancouver under 3000)"):
            st.session_state["messages"].append({"role": "user", "content": prompt})
            try:
                with st.spinner("Calling assistant..."):
                    messages, payload = run_agent_step_mcp_sync_from_streamlit(
                        client, model, st.session_state["messages"]
                    )
            except FuturesTimeoutError:
                st.error("The assistant took too long to respond. Please try again.")
                st.stop()
            except Exception as e:
                st.error(f"Assistant error: {e}")
                st.stop()
            st.session_state["messages"] = messages
            if payload is not None:
                st.session_state["pending_ask"] = payload
            st.rerun()


def run_ui() -> None:
    """Entry point for rental-search-ui-mcp script: start Streamlit server."""
    import streamlit.web.cli as st_cli
    app_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(app_path), "--server.headless", "true"]
    st_cli.main()


if __name__ == "__main__":
    main()
