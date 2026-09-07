"""Streamlit chat UI for the rental search agent. Uses run_agent_step_events from client."""

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

from rental_search_agent.agent import current_date_context, flow_instructions
from rental_search_agent.api_config import has_api_credentials
from rental_search_agent.client import (
    _get_active_search_criteria_from_messages,
    _get_parsed_proximity_rules_from_messages,
    _load_env_file,
    _make_llm_client,
    run_agent_step_events,
)
from rental_search_agent.chat_summary import summarize_conversation_for_preferences
from rental_search_agent.filtering import filter_listings as do_filter_listings
from rental_search_agent.listing_analysis import analyze_listing_against_preferences
from rental_search_agent.proximity_parser import parse_proximity_preferences
from rental_search_agent.semantic_scoring import search_criteria_to_text_blob

# Keys for stored user preferences (viewing time, name, email, phone, proximity, listing preferences)
PREF_KEYS = ("viewing_preference", "name", "email", "phone", "proximity_preferences", "qualitative_preferences")


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
    """Build the search-relevant preferences block to inject into the system message."""
    proximity = (prefs.get("proximity_preferences") or "").strip()
    qualitative = (prefs.get("qualitative_preferences") or "").strip()
    if not proximity and not qualitative:
        return "No stored search preferences (proximity or qualitative)."
    parts = []
    if proximity:
        parts.append(f"proximity_preferences = {proximity!r}")
    if qualitative:
        parts.append(f"qualitative_preferences = {qualitative!r}")
    block = "Stored user preferences: " + "; ".join(parts)
    block += ". Do not ask the user for these again unless they are missing or the user asks to change them. When proximity_preferences is set, parse and apply them (parse_proximity_preferences, geocode, enrich_listings_with_proximity, filter_listings with proximity_rules) after presenting search results. When qualitative_preferences is set, use it for scoring/ranking listings (e.g. call score_listings_by_preferences); do not ask again unless the user changes them."
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
    if "display_list" not in st.session_state:
        st.session_state["display_list"] = []
    if "master_list" not in st.session_state:
        st.session_state["master_list"] = []
    if "display_source" not in st.session_state:
        st.session_state["display_source"] = None
    if "last_sort_by" not in st.session_state:
        st.session_state["last_sort_by"] = None
    if "chat_open" not in st.session_state:
        st.session_state["chat_open"] = True
    if "results_view" not in st.session_state:
        st.session_state["results_view"] = "cards"
    if "map_label_mode" not in st.session_state:
        st.session_state["map_label_mode"] = "price"


def _apply_proximity_filter_safeguard(listings: list[dict], proximity_text: str) -> list[dict]:
    """When display is from enrich and user has proximity prefs, filter to in-range only. Returns filtered list or original on error."""
    if not proximity_text or not listings:
        return listings
    cache = st.session_state.get("proximity_parsed_rules")
    if cache is not None and cache[0] == proximity_text and cache[1]:
        rules = cache[1]
    else:
        try:
            rules = parse_proximity_preferences(proximity_text)
            st.session_state["proximity_parsed_rules"] = (proximity_text, rules)
        except Exception:
            st.warning("Could not parse proximity preferences; showing results unfiltered.")
            return listings
    if not rules:
        return listings
    # Preserve each listing's authoritative 'rank' (assigned by the LLM tool layer in
    # client.py) across this filter round-trip: filter_listings validates listings through
    # the Listing model, which silently drops unrecognized fields like 'rank'. Re-attach by
    # id afterward so the UI keeps labeling listings with the same rank the LLM uses for
    # "listing N" references, even after this safeguard narrows/reorders the set.
    rank_by_id = {
        lst.get("id"): lst.get("rank")
        for lst in listings
        if isinstance(lst, dict) and lst.get("id") is not None
    }
    try:
        resp = do_filter_listings(listings, {}, proximity_rules=rules)
        result = [lst.model_dump() if hasattr(lst, "model_dump") else lst for lst in resp.listings]
        for lst in result:
            if isinstance(lst, dict) and lst.get("id") in rank_by_id:
                lst["rank"] = rank_by_id[lst["id"]]
        return result
    except Exception:
        return listings


def _format_proximity_display(proximity: dict | None) -> str:
    """Format listing.proximity for table display: short summary or 'Distance unknown'."""
    if not proximity or not isinstance(proximity, dict):
        return "—"
    parts = []
    has_unknown = False
    for rule_key, val in proximity.items():
        if val is None:
            has_unknown = True
            continue
        if not isinstance(val, dict):
            has_unknown = True
            continue
        loc = (rule_key.split("|")[0] if "|" in rule_key else rule_key).strip()
        dist = val.get("distance_km")
        dur = val.get("duration_min")
        if dur is not None:
            parts.append(f"{loc}: {float(dur):.0f} min")
        elif dist is not None:
            parts.append(f"{loc}: {float(dist):.1f} km")
        else:
            has_unknown = True
    if has_unknown and not parts:
        return "Distance unknown"
    if has_unknown:
        return "; ".join(parts) + " (some unknown)"
    return "; ".join(parts) if parts else "—"


_TABLE_COL_WIDTHS = [0.5, 0.6, 1.8, 0.8, 0.4, 0.4, 0.6, 0.8, 0.8, 0.8, 1.0, 1.1, 0.8]


def _apply_default_match_score_sort(listings: list[dict]) -> list[dict]:
    """Display-only: sort by semantic_score desc when at least one listing has one,
    else leave order as-is (nothing to sort by, e.g. no qualitative preferences set yet).

    This is a pure Python list sort (no filter_listings/model_dump round-trip), so it
    does not touch each listing's 'rank' field — displayed order changes, but 'rank'
    still correctly identifies each listing for "listing N" references, same guarantee
    the existing proximity closest-first safeguard (_apply_proximity_filter_safeguard)
    provides for that case.
    """
    if not any(isinstance(item, dict) and item.get("semantic_score") is not None for item in listings):
        return listings
    return sorted(
        listings,
        key=lambda item: item.get("semantic_score") if isinstance(item, dict) and item.get("semantic_score") is not None else -1,
        reverse=True,
    )


def _escape_markdown_link_text(text: str) -> str:
    """Escape characters that would let untrusted text break out of a markdown link
    label — e.g. "[label](url)" — and inject a second, attacker-controlled link.

    Security-review/Bugbot finding: the Analyze expander builds a markdown link whose
    *label* is the listing's scraped MLS id (f"[{id}]({url})"); a crafted id containing
    "](attacker-url)[" would close the intended label early and open a new link, so the
    text a user sees as the MLS number could actually navigate elsewhere. Backslash-
    escaping "[", "]", and "\\" itself (the characters CommonMark treats as link-label
    delimiters) neutralizes this while leaving normal MLS ids (plain alphanumeric)
    unchanged.
    """
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _render_clickable_photo(photo_url: str, listing_url: str, width: int) -> None:
    """Render the listing photo as a clickable link to the listing (st.image can't be
    wrapped as a link directly, so this uses escaped raw HTML — same escaping pattern as
    the existing folium map marker links). Falls back to a plain "View" link button when
    there's no photo, so every row/card keeps some click-through to the listing even
    without a photo (this is the table's only click-through now that MLS id is removed)."""
    if photo_url:
        st.markdown(
            f'<a href="{html.escape(listing_url)}" target="_blank" rel="noopener">'
            f'<img src="{html.escape(photo_url)}" width="{width}"></a>',
            unsafe_allow_html=True,
        )
    elif listing_url:
        st.link_button("View", listing_url)
    else:
        st.write("—")


def _format_tags(listing: dict) -> str:
    """Badges for freshness/open-house/price-drop signals; empty string when none apply."""
    badges = []
    age_hours = listing.get("listing_age_hours")
    if age_hours is not None:
        try:
            if float(age_hours) <= 48:
                badges.append("🆕 New")
        except (TypeError, ValueError):
            pass
    if listing.get("open_house"):
        badges.append("🏠 Open house")
    if listing.get("price_change_display"):
        badges.append("↓ Reduced")
    return " · ".join(badges)


def _format_days_on_market(listing: dict) -> str:
    """'Days on Market', approximated from listing_age_hours (parsed from the actor's
    relative freshness text, e.g. '18 hours ago') — the actor has no exact DOM field."""
    age_hours = listing.get("listing_age_hours")
    if age_hours is None:
        return "—"
    try:
        return f"{round(float(age_hours) / 24)}d"
    except (TypeError, ValueError):
        return "—"


def _format_bedrooms(listing: dict) -> str:
    """Bedroom count for display, preserving the source's den notation (e.g. '2 + 1' for 2
    bedrooms + a den) when bedrooms_display is set, so the den isn't silently dropped from
    the UI even though it's excluded from the numeric bedrooms count used for filtering."""
    display = listing.get("bedrooms_display")
    if display:
        return str(display)
    bedrooms = listing.get("bedrooms")
    return str(bedrooms) if bedrooms is not None else "—"


def _format_match_score(listing: dict) -> str:
    """Match score display: semantic_score (0-1) as a whole percentage, or '—' when
    scoring hasn't run yet (e.g. no qualitative preferences set)."""
    score = listing.get("semantic_score")
    if score is None:
        return "—"
    try:
        return f"{round(float(score) * 100)}%"
    except (TypeError, ValueError):
        return "—"


def _format_listing_price(listing: dict) -> str:
    """Human-readable price for table/cards.

    Prefer the numeric ``price`` field so scraped ``price_display`` cannot inject
    Markdown links into any Markdown render path. Fall back to plain display text.
    """
    price = listing.get("price")
    if price is not None:
        try:
            return f"${int(float(price)):,}"
        except (TypeError, ValueError):
            pass
    raw = listing.get("price_display")
    if not raw:
        return "—"
    return str(raw)


def _analyze_button_key(listing: dict, index: int) -> str:
    """Stable unique widget key for Analyze. Empty/None ids must not collide."""
    listing_id = listing.get("id")
    if listing_id is None or listing_id == "":
        return f"analyze_row_{index}"
    return f"analyze_{listing_id}"


def _format_map_price_label(listing: dict) -> str:
    """Compact currency for map pins: $2,800, or $1.25M when price >= 1e6."""
    price = listing.get("price")
    if price is None:
        return "—"
    try:
        value = float(price)
    except (TypeError, ValueError):
        return "—"
    if value >= 1_000_000:
        millions = value / 1_000_000
        formatted = f"{millions:.2f}".rstrip("0").rstrip(".")
        return f"${formatted}M"
    return f"${int(round(value)):,}"


def _render_clickable_address(address: str, listing_url: str) -> None:
    """Address as a new-tab link when a listing URL is present (escaped like map markers)."""
    label = html.escape(address or "—")
    if listing_url:
        st.markdown(
            f'<a href="{html.escape(listing_url)}" target="_blank" rel="noopener">{label}</a>',
            unsafe_allow_html=True,
        )
    else:
        st.write(address or "—")


def _listings_to_table_rows(listings: list[dict]) -> list[dict]:
    """Build table-friendly rows: rank, photo, address, type, bed, bath, size, price,
    days on market, match score, tags, Proximity, URL.

    Uses each listing's 'rank' field (assigned by the LLM tool layer in client.py) rather
    than its position in this list, so numbering stays correct even when this list has been
    locally reordered/filtered for display (e.g. the proximity closest-first safeguard,
    or the default match-score sort), which would otherwise desync the table's numbers
    from what the LLM calls "listing N".
    """
    rows = []
    for i, listing in enumerate(listings):
        bath = listing.get("bathrooms")
        sqft = listing.get("sqft")
        rows.append({
            "rank": listing.get("rank") if listing.get("rank") is not None else i + 1,
            "photo": listing.get("photo_url") or "",
            "address": listing.get("address") or "—",
            "type": listing.get("house_category") or "—",
            "bed": _format_bedrooms(listing),
            "bath": f"{float(bath):g}" if bath is not None else "—",
            "size": str(int(sqft)) if sqft is not None else "—",
            "price": _format_listing_price(listing),
            "days_on_market": _format_days_on_market(listing),
            "match_score": _format_match_score(listing),
            "tags": _format_tags(listing),
            "Proximity": _format_proximity_display(listing.get("proximity")),
            "URL": listing.get("url") or "",
        })
    return rows


def _render_results_table(listings: list[dict]) -> None:
    """Render search results as custom rows with an Analyze button per listing."""
    if not listings:
        return
    # Header row: Rank, Photo, Address, Type, Bed, Bath, Size, Price, Days on Market,
    # Match score, Tags, Proximity, Analyze
    header_cols = st.columns(_TABLE_COL_WIDTHS)
    headers = [
        "Rank", "Photo", "Address", "Type", "Bed", "Bath", "Size", "Price",
        "Days on Market", "Match score", "Tags", "Proximity", "Analyze",
    ]
    for col, label in zip(header_cols, headers):
        with col:
            st.caption(label)
    st.divider()
    for i, listing in enumerate(listings):
        bath = listing.get("bathrooms")
        sqft = listing.get("sqft")
        url = listing.get("url") or ""
        photo_url = listing.get("photo_url") or ""
        prox = _format_proximity_display(listing.get("proximity"))
        tags = _format_tags(listing)
        row_cols = st.columns(_TABLE_COL_WIDTHS)
        with row_cols[0]:
            # Use the listing's authoritative 'rank' (from the LLM tool layer), not this
            # row's position, so the number matches what the LLM calls "listing N" even
            # after a local reorder (e.g. the proximity closest-first safeguard, or the
            # default match-score sort, above).
            st.write(listing.get("rank") if listing.get("rank") is not None else i + 1)
        with row_cols[1]:
            _render_clickable_photo(photo_url, url, width=56)
        with row_cols[2]:
            st.write(listing.get("address") or "—")
        with row_cols[3]:
            st.write(listing.get("house_category") or "—")
        with row_cols[4]:
            st.write(_format_bedrooms(listing))
        with row_cols[5]:
            st.write(f"{float(bath):g}" if bath is not None else "—")
        with row_cols[6]:
            st.write(str(int(sqft)) if sqft is not None else "—")
        with row_cols[7]:
            st.write(_format_listing_price(listing))
        with row_cols[8]:
            st.write(_format_days_on_market(listing))
        with row_cols[9]:
            st.write(_format_match_score(listing))
        with row_cols[10]:
            st.caption(tags or "—")
        with row_cols[11]:
            st.caption(prox)
        with row_cols[12]:
            if st.button("Analyze", key=_analyze_button_key(listing, i)):
                st.session_state["analyze_listing_id"] = listing.get("id")
                st.session_state["analyze_listing"] = listing
                st.rerun()


_CARDS_PER_ROW = 3


def _render_results_cards(listings: list[dict]) -> None:
    """Render search results as a 3-column card grid. Photo and address open the listing."""
    if not listings:
        return
    for row_start in range(0, len(listings), _CARDS_PER_ROW):
        cols = st.columns(_CARDS_PER_ROW)
        chunk = listings[row_start : row_start + _CARDS_PER_ROW]
        for offset, listing in enumerate(chunk):
            with cols[offset]:
                url = listing.get("url") or ""
                photo_url = listing.get("photo_url") or ""
                i = row_start + offset
                rank = listing.get("rank") if listing.get("rank") is not None else i + 1
                bath = listing.get("bathrooms")
                sqft = listing.get("sqft")
                bath_txt = f"{float(bath):g}" if bath is not None else "—"
                size_txt = str(int(sqft)) if sqft is not None else "—"
                with st.container(border=True):
                    _render_clickable_photo(photo_url, url, width=220)
                    st.caption(f"#{rank}")
                    # Use st.write (not Markdown) so scraped price text cannot inject links.
                    st.write(_format_listing_price(listing))
                    _render_clickable_address(listing.get("address") or "—", url)
                    st.caption(
                        f"{_format_bedrooms(listing)} bed · {bath_txt} bath · {size_txt} sqft"
                    )
                    score = _format_match_score(listing)
                    if score != "—":
                        st.caption(f"Match {score}")
                    tags = _format_tags(listing)
                    if tags:
                        st.caption(tags)
                    prox = _format_proximity_display(listing.get("proximity"))
                    if prox and prox != "—":
                        st.caption(prox)
                    if st.button("Analyze", key=_analyze_button_key(listing, i)):
                        st.session_state["analyze_listing_id"] = listing.get("id")
                        st.session_state["analyze_listing"] = listing
                        st.rerun()


def _listings_cache_key(listings: list[dict]) -> str:
    """Stable JSON string for listings, used as cache key. Lists/dicts must be hashable for st.cache_data."""
    return json.dumps(listings, sort_keys=True, default=str)


def _folium_marker_icon(label: str, url: str, label_mode: str):
    """DivIcon for a map pin: circle+bold rank, or rectangle+normal-weight price."""
    url_escaped = html.escape(url or "#")
    label_escaped = html.escape(str(label))
    if label_mode == "price":
        marker_html = (
            '<div style="font-size:12px;font-weight:normal;color:white;text-align:center;'
            "line-height:20px;padding:1px 6px;min-width:54px;height:22px;border-radius:4px;"
            'background-color:#4682B4;border:2px solid white;white-space:nowrap;">'
            f'<a href="{url_escaped}" target="_blank" rel="noopener" '
            f'style="color:white;text-decoration:none;">{label_escaped}</a></div>'
        )
        return folium.DivIcon(icon_size=(72, 26), icon_anchor=(36, 13), html=marker_html)
    marker_html = (
        '<div style="font-size:14pt;font-weight:bold;color:white;text-align:center;'
        "line-height:30px;width:30px;height:30px;border-radius:50%;"
        'background-color:#4682B4;border:2px solid white;">'
        f'<a href="{url_escaped}" target="_blank" rel="noopener" '
        f'style="color:white;text-decoration:none;">{label_escaped}</a></div>'
    )
    return folium.DivIcon(icon_size=(32, 32), icon_anchor=(16, 16), html=marker_html)


def _add_folium_markers(m, map_points: list[dict], label_mode: str) -> None:
    for pt in map_points:
        folium.Marker(
            location=[pt["lat"], pt["lon"]],
            icon=_folium_marker_icon(pt["label"], pt.get("url") or "#", label_mode),
        ).add_to(m)


@st.cache_data(show_spinner=False)
def _get_map_html_cached(listings_json: str, label_mode: str = "rank") -> str | None:
    """Build Folium map HTML from listings. Returns None if no map or Folium unavailable.
    Cached by listings content and label_mode so toggling rank/price rebuilds pins."""
    if folium is None:
        return None
    listings = json.loads(listings_json) if listings_json else []
    map_points, center_lat, center_lon = _build_map_data(listings, label_mode=label_mode)
    if not map_points or center_lat is None or center_lon is None:
        return None
    m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
    _add_folium_markers(m, map_points, label_mode)
    return m._repr_html_()


def _build_map_data(
    listings: list[dict], label_mode: str = "rank"
) -> tuple[list[dict], float | None, float | None]:
    """Build list of {lat, lon, label, url} for listings with valid coordinates.
    Returns (map_points, center_lat, center_lon). Center is None if no points.

    Rank labels use each listing's 'rank' field (assigned by the LLM tool layer in client.py)
    rather than position in this list, so map pin numbers stay correct even when this list
    has been locally reordered for display (e.g. the proximity closest-first safeguard).
    Price labels use compact currency from _format_map_price_label. Default label_mode is
    "rank" so existing unit tests stay valid; the UI passes the session value (price by default).
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
        if label_mode == "price":
            label = _format_map_price_label(listing)
        else:
            label = str(listing.get("rank")) if listing.get("rank") is not None else str(i + 1)
        points.append({"lat": lat, "lon": lon, "label": label, "url": url})
        lats.append(lat)
        lons.append(lon)
    if not points:
        return points, None, None
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    return points, center_lat, center_lon


def _render_results_map(
    map_points: list[dict],
    center_lat: float,
    center_lon: float,
    label_mode: str = "rank",
) -> None:
    """Render a map with points labeled by rank or price (see _build_map_data).
    Uses Folium for reliable label rendering; falls back to PyDeck if Folium is not available."""
    if folium is not None:
        m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
        _add_folium_markers(m, map_points, label_mode)
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
            width='stretch',
            height=400,
        )
        return
    st.caption("Map unavailable: install folium (recommended) or pydeck to show results on a map.")


def _inject_chat_blob_css() -> None:
    """Dock the chat launcher/panel to the viewport.

    Use attribute selectors — Streamlit's st-key-* class may sit on a wrapper.
    Prefer theme CSS variables for opaque backgrounds so light/dark both stay readable.
    When the panel is open, reserve right padding so results are not covered.
    """
    chat_open = st.session_state.get("chat_open", True)
    # Keep results clear of the fixed ~420px panel while chat is open.
    pad_right = "min(440px, calc(100vw - 1.5rem))" if chat_open else "1rem"
    st.markdown(
        f"""
        <style>
        [class*="st-key-chat_blob"] {{
            position: fixed !important;
            /* Clear Streamlit's top toolbar (Deploy / Stop / menu) so the collapse
               control is not covered. Header is typically ~2.875–3.5rem. */
            top: 3.75rem !important;
            right: 0.75rem !important;
            bottom: 0.75rem !important;
            left: auto !important;
            height: auto !important;
            max-height: none !important;
            width: min(420px, calc(100vw - 1.5rem)) !important;
            z-index: 10000 !important;
            background-color: var(--secondary-background-color, #0e1117) !important;
            border: 1px solid rgba(250, 250, 250, 0.18) !important;
            border-radius: 12px !important;
            box-shadow: 0 8px 28px rgba(0, 0, 0, 0.45) !important;
            padding: 0.6rem 0.75rem 0.75rem !important;
            overflow: visible !important;
        }}
        [class*="st-key-chat_blob"] [data-testid="stVerticalBlock"],
        [class*="st-key-chat_blob"] [data-testid="stVerticalBlockBorderWrapper"] {{
            background-color: var(--secondary-background-color, #0e1117) !important;
        }}
        [class*="st-key-chat_history"] {{
            height: calc(100vh - 16rem) !important;
            max-height: calc(100vh - 16rem) !important;
            overflow: auto !important;
        }}
        [data-baseweb="popover"],
        [data-baseweb="menu"],
        [data-testid="stSelectboxVirtualDropdown"],
        [data-testid="stMultiSelect"] [data-baseweb="popover"] {{
            z-index: 2147483647 !important;
        }}
        /* Exact launcher container only — do not match chat_open_btn / chat_close_btn. */
        [class*="st-key-chat_launcher"] {{
            position: fixed !important;
            bottom: 1.25rem !important;
            right: 1.25rem !important;
            z-index: 10000 !important;
            width: auto !important;
            background-color: var(--secondary-background-color, #0e1117) !important;
            border: 1px solid rgba(250, 250, 250, 0.18) !important;
            border-radius: 24px !important;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45) !important;
            padding: 0.35rem 0.5rem !important;
        }}
        .block-container {{
            padding-bottom: 6rem;
            padding-right: {pad_right} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _apply_listing_state(listing_state: dict | None) -> None:
    """Copy listing_state from an agent step into session_state."""
    if listing_state is None:
        return
    if listing_state.get("display_source") is not None:
        st.session_state["display_list"] = listing_state.get("display_list", [])
        st.session_state["display_source"] = listing_state.get("display_source")
        st.session_state["last_sort_by"] = listing_state.get("last_sort_by")
    if "master_list" in listing_state:
        st.session_state["master_list"] = listing_state.get("master_list") or []


def _run_user_prompt(client, model, prompt: str) -> None:
    """Append a user message, run one agent step, and rerun."""
    st.session_state["messages"].append({"role": "user", "content": prompt})
    payload, listing_state = _run_agent_step_with_ui(client, model)
    _apply_listing_state(listing_state)
    if payload is not None:
        st.session_state["pending_ask"] = payload
    st.rerun()


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


def _run_agent_step_with_ui(client, model) -> tuple[dict | None, dict | None]:
    """Run one agent step against st.session_state['messages'] with live UI feedback: an
    expandable checklist of tool calls as they run (e.g. "Searching for listings...") and the
    assistant's final text streamed in as it arrives. Updates st.session_state['messages'] in
    place. Returns (ask_user_payload, listing_state) — same info run_agent_step returns besides
    messages, which is already applied to session state."""
    step_placeholders: dict[int, "st.delta_generator.DeltaGenerator"] = {}
    final_event: dict | None = None
    with st.chat_message("assistant"):
        status_box = st.status("Working...", expanded=True)
        text_placeholder = st.empty()
        acc_text = ""
        for event in run_agent_step_events(client, model, st.session_state["messages"], stream=True):
            etype = event["type"]
            if etype in ("round_start", "text_reset"):
                # round_start: a new LLM round is starting; any text streamed so far belongs to
                # a distinct, separately-persisted assistant message (e.g. rare preamble
                # alongside a tool call).
                # text_reset: a malformed streamed tool call triggered a non-streaming fallback
                # retry within the *same* round; the fallback's text_delta is the full,
                # authoritative reply and must replace (not append to) the partial text already
                # streamed from the failed attempt.
                # Either way, reset so stale/partial text isn't concatenated with what follows.
                acc_text = ""
                text_placeholder.empty()
            elif etype == "tool_start":
                ph = status_box.empty()
                ph.markdown(f"- \u23f3 {event['label']}")
                step_placeholders[event["seq"]] = ph
                status_box.update(label=event["label"])
            elif etype == "tool_end":
                ph = step_placeholders.get(event["seq"])
                if ph is not None:
                    icon = "\u2705" if event["ok"] else "\u26a0\ufe0f"
                    ph.markdown(f"- {icon} {event['label']}")
            elif etype == "text_delta":
                acc_text += event["delta"]
                text_placeholder.markdown(acc_text)
            elif etype == "done":
                final_event = event
        status_box.update(label="Done", state="complete", expanded=False)
        if not acc_text:
            # No streamed text (e.g. the turn ended on ask_user) — nothing more to show here.
            text_placeholder.empty()
    assert final_event is not None  # run_agent_step_events always ends with a "done" event
    st.session_state["messages"] = final_event["messages"]
    return final_event.get("ask_user_payload"), final_event.get("listing_state")


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
        st.caption(
            "Proximity and listing preferences are used by the chat assistant for search and ranking. "
            "Preferred viewing times and contact details are saved for upcoming booking features and are not used in chat."
        )
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
        if not st.session_state.get("chat_open", True):
            if st.button("Open chat", key="sidebar_chat_open"):
                st.session_state["chat_open"] = True
                st.rerun()


def _render_ask_form(pending: dict) -> None:
    """Show form for ask_user: prompt + input/selectbox/multiselect. On submit, append tool result and run step."""
    st.markdown(f"**{pending['prompt']}**")
    choices = pending.get("choices") or []
    allow_multiple = pending.get("allow_multiple", False)
    # Include tool_call_id so widget state does not leak across different ask_user prompts.
    ask_key = pending.get("tool_call_id") or "ask"

    with st.form("ask_user_form", clear_on_submit=True):
        if choices:
            if allow_multiple:
                selected = st.multiselect(
                    "Select one or more", choices, key=f"ask_multiselect_{ask_key}"
                )
                submit_val = selected
            else:
                selected = st.selectbox(
                    "Choose one", [""] + choices, key=f"ask_selectbox_{ask_key}"
                )
                submit_val = selected if selected else None
        else:
            submit_val = st.text_input("Your answer", key=f"ask_text_{ask_key}")

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
            # Run step in a loop until no more pending ask (or we get final reply)
            while True:
                payload, listing_state = _run_agent_step_with_ui(client, model)
                _apply_listing_state(listing_state)
                if payload is not None:
                    st.session_state["pending_ask"] = payload
                    st.rerun()
                break
            st.rerun()


def _render_chat_panel(client, model) -> None:
    """Collapsed FAB or open bottom-right chat panel (history + ask_user + send form)."""
    if not st.session_state.get("chat_open", True):
        with st.container(key="chat_launcher"):
            label = "Chat •" if st.session_state.get("pending_ask") else "Open chat"
            if st.button(label, key="chat_open_btn", type="primary"):
                st.session_state["chat_open"] = True
                st.rerun()
        return

    with st.container(key="chat_blob"):
        head_col, collapse_col = st.columns([4, 1])
        with head_col:
            st.markdown("**Chat**")
        with collapse_col:
            if st.button("–", key="chat_close_btn", help="Collapse chat"):
                st.session_state["chat_open"] = False
                st.rerun()
        # Height is driven by CSS on st-key-chat_history so short viewports do not fight
        # a fixed 720px Python height against the full-height dock.
        with st.container(key="chat_history"):
            _render_chat_history()
            pending_prompt = st.session_state.pop("pending_chat_prompt", None)
            if pending_prompt:
                _run_user_prompt(client, model, pending_prompt)
        pending = st.session_state.get("pending_ask")
        if pending is not None:
            _render_ask_form(pending)
            return
        with st.form("chat_send_form", clear_on_submit=True):
            prompt = st.text_input(
                "Message",
                label_visibility="collapsed",
                placeholder="e.g. 2 bed rental in Vancouver under 3000",
            )
            submitted = st.form_submit_button("Send")
            if submitted and (prompt or "").strip():
                st.session_state["pending_chat_prompt"] = prompt.strip()
                st.rerun()


def main() -> None:
    st.set_page_config(page_title="Property Search Assistant", page_icon="🏠", layout="wide")
    _ensure_env_loaded()
    _init_session_state()
    _inject_chat_blob_css()
    st.title("Property Search Assistant")

    _render_preferences_sidebar()

    client, model = _get_client_and_model()
    if client is None or model is None:
        st.error("Set API_PROVIDER (openrouter or openai) and the corresponding API key (OPENROUTER_API_KEY or OPENAI_API_KEY) in .env to run the assistant.")
        st.stop()

    prefs = st.session_state.get("user_preferences") or {}
    listings = st.session_state.get("display_list") or []
    proximity_text = (prefs.get("proximity_preferences") or "").strip()
    display_source = st.session_state.get("display_source")
    # Optional safeguard: when display is from enrich and proximity prefs set, apply filter locally.
    # Note: this must NOT independently re-sort listings (e.g. "closest first") — the
    # LLM is instructed (agent.py step 4p) to sort_by="proximity" itself as part of its
    # post-enrich filter_listings call, so its canonical order (and each listing's
    # 'rank', which the LLM uses for "listing N" references) is already nearest-first.
    # A separate local sort here would visually reorder rows without renumbering rank,
    # making the Rank column look unsorted even though it's still correctly identifying
    # each listing.
    if proximity_text and display_source == "enrich" and listings:
        listings = _apply_proximity_filter_safeguard(listings, proximity_text)
    # Default display sort: rank by match score (semantic_score) when available, so
    # the best qualitative matches surface first in both the table and the map. This
    # is display-only (see _apply_default_match_score_sort docstring re: 'rank').
    # Bugbot regression guard: only apply this fallback when no *explicit* non-score
    # sort is currently active (e.g. the agent just ran filter_listings with
    # sort_by="price"/"proximity"/etc.) — otherwise this would silently clobber that
    # explicit sort and desync the table from what the agent told the user it did.
    last_sort_by = st.session_state.get("last_sort_by")
    if last_sort_by is None or last_sort_by == "semantic_score":
        listings = _apply_default_match_score_sort(listings)

    # Analysis card at top: when user clicked Analyze, run analysis and show result
    # before the search results so the detail view is immediately visible.
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
                        # Build the same listing-blob-shaped embedding query
                        # score_listings_by_preferences uses for the table's
                        # semantic_score/"Match score" column (bed/bath/sqft/price/
                        # location + qualitative preferences + proximity), reusing the
                        # same message-history reconstruction so both surfaces stay
                        # consistent for the same listing/preferences. preferences_text
                        # above (which also folds in proximity) still drives the
                        # narrative key_matches/key_gaps, unaffected by this override.
                        chat_messages = st.session_state.get("messages") or []
                        search_criteria = _get_active_search_criteria_from_messages(chat_messages)
                        proximity_rules = _get_parsed_proximity_rules_from_messages(chat_messages)
                        score_query_text = search_criteria_to_text_blob(
                            search_criteria, qualitative, proximity_rules
                        )
                        result = analyze_listing_against_preferences(
                            analyze_listing,
                            preferences_text,
                            conversation_context=conversation_context or None,
                            score_query_text=score_query_text or None,
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
                        photo_url = analyze_listing.get("photo_url") or ""
                        _render_clickable_photo(photo_url, analyze_listing.get("url") or "", width=240)
                        detail_bits = []
                        if analyze_listing.get("id") and analyze_listing.get("url"):
                            mls_label = _escape_markdown_link_text(str(analyze_listing["id"]))
                            detail_bits.append(f"**MLS:** [{mls_label}]({analyze_listing['url']})")
                        if analyze_listing.get("property_category"):
                            detail_bits.append(f"**Type:** {analyze_listing['property_category']}")
                        if analyze_listing.get("lot_size"):
                            detail_bits.append(f"**Lot size:** {analyze_listing['lot_size']}")
                        if analyze_listing.get("listing_age_display"):
                            detail_bits.append(f"**Listed:** {analyze_listing['listing_age_display']}")
                        if analyze_listing.get("price_change_display"):
                            detail_bits.append(f"**Price change:** {analyze_listing['price_change_display']}")
                        if analyze_listing.get("open_house"):
                            detail_bits.append(f"**Open house:** {analyze_listing['open_house']}")
                        if analyze_listing.get("agent_name"):
                            agent_bit = f"**Listing agent:** {analyze_listing['agent_name']}"
                            if analyze_listing.get("agent_phone"):
                                agent_bit += f" ({analyze_listing['agent_phone']})"
                            detail_bits.append(agent_bit)
                        if analyze_listing.get("brokerage_name"):
                            detail_bits.append(f"**Brokerage:** {analyze_listing['brokerage_name']}")
                        if analyze_listing.get("video_url"):
                            detail_bits.append(f"[Video / virtual tour]({analyze_listing['video_url']})")
                        if detail_bits:
                            st.markdown(" &nbsp;|&nbsp; ".join(detail_bits))
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

    if listings:
        st.segmented_control(
            "Results view",
            options=["cards", "table"],
            format_func=lambda x: "Cards" if x == "cards" else "Table",
            key="results_view",
        )
        with st.expander("Search results", expanded=True):
            if st.session_state.get("results_view") == "table":
                _render_results_table(listings)
            else:
                _render_results_cards(listings)
        label_mode = st.session_state.get("map_label_mode") or "price"
        map_points, center_lat, center_lon = _build_map_data(listings, label_mode=label_mode)
        if map_points and center_lat is not None and center_lon is not None:
            with st.expander("Search results map", expanded=True):
                st.segmented_control(
                    "Map labels",
                    options=["price", "rank"],
                    format_func=lambda x: "Price" if x == "price" else "Rank",
                    key="map_label_mode",
                )
                label_mode = st.session_state.get("map_label_mode") or "price"
                map_points, center_lat, center_lon = _build_map_data(
                    listings, label_mode=label_mode
                )
                if folium is not None:
                    map_html = _get_map_html_cached(_listings_cache_key(listings), label_mode)
                    if map_html:
                        st.components.v1.html(map_html, height=400, scrolling=False)
                    else:
                        _render_results_map(map_points, center_lat, center_lon, label_mode)
                else:
                    _render_results_map(map_points, center_lat, center_lon, label_mode)
        elif not map_points:
            with st.expander("Search results map", expanded=False):
                st.caption("No map: addresses have no coordinates.")
    else:
        st.caption("Run a search to see results here.")

    _render_chat_panel(client, model)


def run_ui() -> None:
    """Entry point for rental-search-ui script: start Streamlit server."""
    import sys
    import streamlit.web.cli as st_cli
    app_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(app_path), "--server.headless", "true"]
    st_cli.main()


if __name__ == "__main__":
    main()
