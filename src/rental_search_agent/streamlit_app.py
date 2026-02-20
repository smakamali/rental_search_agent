"""Streamlit chat UI for the rental search agent. Uses run_agent_step from client."""

import html
import json
import os
from pathlib import Path

import streamlit as st

try:
    import folium
except ImportError:
    folium = None
try:
    import pydeck as pdk
except ImportError:
    pdk = None

from rental_search_agent.agent import flow_instructions
from rental_search_agent.client import _load_env_file, _make_llm_client, run_agent_step

# Keys for stored user preferences (viewing time, name, email, phone)
PREF_KEYS = ("viewing_preference", "name", "email", "phone")


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
    if not viewing and not name and not email:
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
    block = "Stored user preferences: " + "; ".join(parts)
    block += ". Use these values when calling simulate_viewing_request or when presenting options; do not ask the user for these again unless they are missing or the user asks to change them."
    return block


def _build_system_content() -> str:
    """System message content: flow instructions + current preferences block."""
    prefs = st.session_state.get("user_preferences") or {k: "" for k in PREF_KEYS}
    return flow_instructions() + "\n\n" + _preferences_block(prefs)


def _ensure_env_loaded() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    _load_env_file(project_root / ".env")


def _get_client_and_model():
    """Return (client, model), cached in session state. Ensures env is loaded first."""
    if "llm_client" in st.session_state and "llm_model" in st.session_state:
        return st.session_state["llm_client"], st.session_state["llm_model"]
    _ensure_env_loaded()
    if not os.environ.get("OPENROUTER_API_KEY", "").strip() and not os.environ.get("OPENAI_API_KEY", "").strip():
        return None, None
    client, model = _make_llm_client()
    st.session_state["llm_client"] = client
    st.session_state["llm_model"] = model
    return client, model


def _init_session_state() -> None:
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


def _get_latest_search_listings(messages: list[dict]) -> list[dict]:
    """Extract the most recent rental_search or filter_listings result from message history.
    Returns the 'listings' array (list of listing dicts) or empty list if none found.
    """
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
            "bath": str(int(bath)) if bath is not None else "—",
            "size": str(int(sqft)) if sqft is not None else "—",
            "rent": rent,
            "URL": listing.get("url") or "",
        })
    return rows


def _render_results_table(listings: list[dict]) -> None:
    """Render search results as a dataframe with rank, MLS id, address, bed, bath, size, rent, URL link."""
    if not listings:
        return
    rows = _listings_to_table_rows(listings)
    st.dataframe(
        rows,
        column_config={
            "rank": st.column_config.NumberColumn("Rank", format="%d"),
            "MLS id": st.column_config.TextColumn("MLS id"),
            "address": st.column_config.TextColumn("Address"),
            "bed": st.column_config.TextColumn("Bed"),
            "bath": st.column_config.TextColumn("Bath"),
            "size": st.column_config.TextColumn("Size (sqft)"),
            "rent": st.column_config.TextColumn("Rent"),
            "URL": st.column_config.LinkColumn("URL", display_text="Link"),
        },
        use_container_width=True,
        hide_index=True,
    )


def _build_map_data(listings: list[dict]) -> tuple[list[dict], float | None, float | None]:
    """Build list of {lat, lon, label, url} for listings with valid coordinates.
    Returns (map_points, center_lat, center_lon). Center is None if no points.
    """
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
    """Render a map with points labeled by listing order (1, 2, 3, ...). Uses Folium for reliable label rendering; falls back to PyDeck if Folium is not available."""
    if folium is not None:
        # Folium: markers with DivIcon so all numbers (1–9, 10, 11, ...) render correctly
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
        # Fallback: PyDeck (labels 10+ may not render due to deck.gl TextLayer bug)
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
            use_container_width=True,
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
    """Sidebar form to set or edit viewing time, name, email, phone. Saves to session and optional file."""
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
            submitted = st.form_submit_button("Save")
            if submitted:
                new_prefs = {
                    "viewing_preference": (viewing or "").strip(),
                    "name": (name or "").strip(),
                    "email": (email or "").strip(),
                    "phone": (phone or "").strip(),
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
                st.error("Set OPENROUTER_API_KEY or OPENAI_API_KEY in .env or environment.")
                st.stop()
            # Run step in a loop until no more pending ask (or we get final reply)
            while True:
                messages, payload = run_agent_step(client, model, st.session_state["messages"])
                st.session_state["messages"] = messages
                if payload is not None:
                    st.session_state["pending_ask"] = payload
                    st.rerun()
                break
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Rental Search Assistant", page_icon="🏠")
    st.title("Rental Search Assistant")

    _ensure_env_loaded()
    _init_session_state()
    _render_preferences_sidebar()

    client, model = _get_client_and_model()
    if client is None or model is None:
        st.error("Set OPENROUTER_API_KEY (recommended) or OPENAI_API_KEY in .env or environment to run the assistant.")
        st.stop()

    _render_chat_history()

    # Search results: table (above) then map
    listings = _get_latest_search_listings(st.session_state["messages"])
    if listings:
        with st.expander("Search results table", expanded=True):
            _render_results_table(listings)
    map_points, center_lat, center_lon = _build_map_data(listings)
    if map_points and center_lat is not None and center_lon is not None:
        with st.expander("Search results map", expanded=True):
            _render_results_map(map_points, center_lat, center_lon)
    elif listings and not map_points:
        with st.expander("Search results map", expanded=False):
            st.caption("No map: addresses have no coordinates.")

    pending = st.session_state.get("pending_ask")
    if pending is not None:
        with st.chat_message("assistant"):
            _render_ask_form(pending)
        return

    if prompt := st.chat_input("Type your search request (e.g. 2 bed in Vancouver under 3000)"):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        messages, payload = run_agent_step(client, model, st.session_state["messages"])
        st.session_state["messages"] = messages
        if payload is not None:
            st.session_state["pending_ask"] = payload
        st.rerun()


def run_ui() -> None:
    """Entry point for rental-search-ui script: start Streamlit server."""
    import sys
    import streamlit.web.cli as st_cli
    app_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(app_path), "--server.headless", "true"]
    st_cli.main()


if __name__ == "__main__":
    main()
