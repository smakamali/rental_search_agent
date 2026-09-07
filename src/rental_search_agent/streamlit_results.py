"""Streamlit search-results UI: formatters, Grid/Table/Map renderers, shared view state."""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from typing import Any, Literal

import streamlit as st

try:
    import folium
except ImportError:
    folium = None
try:
    import pydeck as pdk
except ImportError:
    pdk = None

from rental_search_agent.display_format import (
    format_count,
    format_currency,
    format_duration,
    format_percentage,
    format_sqft,
    get_score_color,
    proximity_criterion_name,
    safe_http_url,
    score_to_pct,
    split_listing_address,
)
from rental_search_agent.geocoding import NEAREST_TRANSIT_LOCATION

RESULTS_VIEW_ALIASES = {"cards": "grid"}
VALID_RESULTS_VIEWS = ("grid", "table", "map")
WIDGET_RESULTS_VIEWS = ("grid", "table", "map")
VALID_MAP_LABEL_MODES = ("price", "match", "rank")
WIDGET_MAP_LABEL_MODES = ("price", "match", "rank")
_VIEW_LABELS = {"grid": "Grid", "table": "Table", "map": "Map"}
_MAP_LABEL_LABELS = {"price": "Price", "match": "Match", "rank": "Rank"}
_MAP_SUMMARY_PER_ROW = 4
_MAP_MARKER_BLUE = "#4682B4"

_SORT_BY_LABELS = {
    "semantic_score": "Match",
    "match_score": "Match",
    "proximity": "Proximity",
    "price": "Price",
    "listing_age_hours": "Newest",
    "bedrooms": "Bedrooms",
    "bathrooms": "Bathrooms",
    "sqft": "Size",
    "address": "Address",
}

_CARDS_PER_ROW = 3
_TRANSIT_LOCATION_ALIASES = frozenset(
    {
        NEAREST_TRANSIT_LOCATION,
        "nearest transit",
        "transit",
    }
)
_MODE_WORDS = {
    "walk": "walk",
    "walking": "walk",
    "drive": "drive",
    "driving": "drive",
    "transit": "transit",
}
_UNAVAILABLE_TOKENS = frozenset({"none", "nan", "null", "n/a", "nat"})


def _is_finite_number(value: Any) -> bool:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(n)


def _finite_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if not _is_finite_number(value):
        return None
    return float(value)


def _plain_display_text(value: Any, fallback: str = "—") -> str:
    """Coerce scraped values for UI. Never returns None/NaN/null tokens."""
    if value is None or isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            return fallback
        text = str(int(value)) if float(value) == int(value) else str(value)
    else:
        text = str(value).strip()
    if not text or text.lower() in _UNAVAILABLE_TOKENS:
        return fallback
    return text


def normalize_results_view(value: str | None) -> str:
    """Canonical results view: cards → grid; unknown values fall back to grid."""
    raw = (value or "").strip().lower()
    raw = RESULTS_VIEW_ALIASES.get(raw, raw)
    if raw in VALID_RESULTS_VIEWS:
        return raw
    return "grid"


def normalize_map_label_mode(value: str | None) -> str:
    """Canonical map label mode; unknown/stale values fall back to price."""
    raw = (value or "").strip().lower()
    if raw in VALID_MAP_LABEL_MODES:
        return raw
    return "price"


def current_results_view(session_state: Any) -> str:
    """Read/normalize results_view without writing. Safe after the widget exists."""
    view = normalize_results_view(session_state.get("results_view"))
    if view not in WIDGET_RESULTS_VIEWS:
        return "grid"
    return view


def prepare_results_widget_state(session_state: Any) -> str:
    """Coerce persisted results_view before the Grid/Table/Map control is created.

    Must not write after ``st.segmented_control(key="results_view")`` is instantiated.
    """
    view = current_results_view(session_state)
    if session_state.get("results_view") != view:
        session_state["results_view"] = view
    return view


def results_count_label(count: int) -> str:
    if count == 1:
        return "1 property"
    return f"{count} properties"


def ordered_by_caption(sort_by: str | None) -> str | None:
    """Passive ordering hint from last_sort_by. None when the mapping is unknown."""
    label = format_sort_by_label(sort_by)
    if not label:
        return None
    return f"Ordered by {label.lower()}"


def current_map_label_mode(session_state: Any) -> str:
    """Read/normalize map_label_mode without writing. Safe after the widget exists."""
    mode = normalize_map_label_mode(session_state.get("map_label_mode"))
    if mode not in WIDGET_MAP_LABEL_MODES:
        return "price"
    return mode


def prepare_map_label_widget_state(session_state: Any) -> str:
    """Coerce persisted map_label_mode before the Price/Match/Rank control is created.

    Must not write after ``st.segmented_control(key="map_label_mode")`` is instantiated.
    """
    mode = current_map_label_mode(session_state)
    if session_state.get("map_label_mode") != mode:
        session_state["map_label_mode"] = mode
    return mode


def format_map_coverage(total: int, mapped: int) -> str:
    """User-facing count of plotted vs unmappable listings."""
    skipped = max(0, int(total) - int(mapped))
    if skipped == 0:
        if mapped == 1:
            return "1 result on map"
        return f"{mapped} results on map"
    skipped_txt = (
        "1 listing has no mappable location"
        if skipped == 1
        else f"{skipped} listings have no mappable location"
    )
    return f"{mapped} of {total} results on map · {skipped_txt}"


def format_sort_by_label(sort_by: str | None) -> str | None:
    """Display-only label for last_sort_by. Does not change sort behavior."""
    if not sort_by:
        return None
    return _SORT_BY_LABELS.get(str(sort_by).strip())


def listing_match_score(listing_or_score: Any) -> float | None:
    """Overall Match: match_score preferred, semantic_score fallback."""
    if listing_or_score is None:
        return None
    if isinstance(listing_or_score, dict):
        score = listing_or_score.get("match_score")
        if score is None:
            score = listing_or_score.get("semantic_score")
        return _finite_or_none(score)
    return _finite_or_none(listing_or_score)


def match_score_display(listing_or_score: Any) -> tuple[int | None, str]:
    """Return (percent, get_score_color hex) for compact Match visualization."""
    score = listing_match_score(listing_or_score)
    pct = score_to_pct(score) if score is not None else None
    color = get_score_color(pct) if pct is not None else "#888888"
    return pct, color


def _format_match_score(listing: dict) -> str:
    """Match score display: overall match_score (fallback semantic_score) as %, or '—'."""
    pct, _color = match_score_display(listing)
    if pct is None:
        return "—"
    return format_percentage(pct)


def compact_match_score_html(
    listing_or_score: Any,
    *,
    size: int = 36,
    show_label: bool = True,
) -> str:
    """Small ring + percentage for Grid/Table. Uses shared score colors."""
    pct, color = match_score_display(listing_or_score)
    pct_text = "—" if pct is None else f"{pct}%"
    aria = (
        "Match score: unavailable"
        if pct is None
        else f"Match score: {pct} percent"
    )
    r = 14.0
    c = 2.0 * math.pi * r
    shown = 0 if pct is None else max(0, min(100, pct))
    dash = c * (shown / 100.0)
    gap = c - dash
    label_html = (
        '<span class="rsa-match-compact-label">Match</span>' if show_label else ""
    )
    return (
        f'<div class="rsa-match-compact" role="img" aria-label="{html.escape(aria)}">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 36 36" aria-hidden="true">'
        f'<circle cx="18" cy="18" r="{r:.1f}" fill="none" stroke="currentColor" '
        f'stroke-opacity="0.18" stroke-width="3.5"/>'
        f'<circle cx="18" cy="18" r="{r:.1f}" fill="none" stroke="{html.escape(color)}" '
        f'stroke-width="3.5" stroke-linecap="round" '
        f'stroke-dasharray="{dash:.2f} {gap:.2f}" transform="rotate(-90 18 18)"/>'
        f"</svg>"
        f'<span class="rsa-match-compact-pct">{html.escape(pct_text)}</span>'
        f"{label_html}</div>"
    )


def render_compact_match_score(
    listing_or_score: Any,
    *,
    size: int = 36,
    show_label: bool = True,
) -> None:
    """Render the compact Match indicator (not the large Analyze gauge)."""
    st.markdown(
        compact_match_score_html(listing_or_score, size=size, show_label=show_label),
        unsafe_allow_html=True,
    )


def listing_address_parts(listing: dict) -> tuple[str, str]:
    """Street / locality using split_listing_address. Empty address stays '—'."""
    address = listing.get("address") if isinstance(listing, dict) else None
    postal = listing.get("postal_code") if isinstance(listing, dict) else None
    address_text = _plain_display_text(address, fallback="")
    postal_text = _plain_display_text(postal, fallback="")
    if not address_text:
        return ("—", postal_text)
    return split_listing_address(address_text, postal_text or None)


def listing_rank(listing: dict, fallback_index: int) -> Any:
    """Authoritative listing['rank']; position is only a missing-field fallback."""
    rank = listing.get("rank") if isinstance(listing, dict) else None
    if rank is None or rank == "":
        return fallback_index + 1
    if _is_finite_number(rank):
        n = float(rank)
        return int(n) if n == int(n) else n
    return fallback_index + 1


def listing_result_identity(listing: dict, fallback_index: int) -> dict[str, Any]:
    """Canonical rank/Match shared by Grid, Table, and Map for one listing."""
    pct, color = match_score_display(listing)
    return {
        "id": listing.get("id") if isinstance(listing, dict) else None,
        "rank": listing_rank(listing, fallback_index),
        "match_pct": pct,
        "match_color": color,
    }


def request_listing_analysis(listing: dict) -> None:
    """Shared Analyze action: same session keys as Grid/Table Analyze buttons."""
    st.session_state["analyze_listing_id"] = listing.get("id")
    st.session_state["analyze_listing"] = listing
    st.rerun()


@dataclass(frozen=True)
class ProximityDisplayItem:
    location: str
    mode: str
    status: Literal["available", "unavailable"]
    duration_min: float | None
    distance_km: float | None
    display_text: str
    unavailable_text: str | None


def _is_nearest_transit(location: str) -> bool:
    loc = (location or "").strip().lower()
    return loc in _TRANSIT_LOCATION_ALIASES or loc.startswith("nearest transit")


def _split_proximity_key(rule_key: Any) -> tuple[str, str]:
    raw = str(rule_key or "").strip()
    if "|" in raw:
        location, mode = raw.split("|", 1)
        return location.strip(), mode.strip().lower()
    return raw, ""


def _available_proximity_text(
    location: str,
    mode: str,
    duration_min: float | None,
    distance_km: float | None,
) -> str:
    transit = _is_nearest_transit(location)
    mode_word = _MODE_WORDS.get(mode, mode)
    if duration_min is not None:
        dur = format_duration(duration_min)
        if transit:
            if mode_word:
                return f"{dur} {mode_word} to transit"
            return f"{dur} to transit"
        return f"{dur} to {location}"
    if distance_km is not None:
        dist = f"{float(distance_km):.1f} km"
        if transit:
            if mode_word:
                return f"{dist} {mode_word} to transit"
            return f"{dist} to transit"
        return f"{dist} to {location}"
    return ""


def parse_proximity_display(proximity: dict | None) -> list[ProximityDisplayItem]:
    """Structured proximity rows for every results view. Never emits '(some unknown)'."""
    if not proximity or not isinstance(proximity, dict):
        return []
    items: list[ProximityDisplayItem] = []
    for rule_key, val in proximity.items():
        location, mode = _split_proximity_key(rule_key)
        if not location:
            location = "location"
        criterion = proximity_criterion_name(mode or None, location)
        unavailable_text = f"{criterion} unavailable"
        duration_min = None
        distance_km = None
        if isinstance(val, dict):
            duration_min = _finite_or_none(val.get("duration_min"))
            distance_km = _finite_or_none(val.get("distance_km"))
        if duration_min is None and distance_km is None:
            items.append(
                ProximityDisplayItem(
                    location=location,
                    mode=mode,
                    status="unavailable",
                    duration_min=None,
                    distance_km=None,
                    display_text=unavailable_text,
                    unavailable_text=unavailable_text,
                )
            )
            continue
        items.append(
            ProximityDisplayItem(
                location=location,
                mode=mode,
                status="available",
                duration_min=duration_min,
                distance_km=distance_km,
                display_text=_available_proximity_text(
                    location, mode, duration_min, distance_km
                ),
                unavailable_text=None,
            )
        )
    return items


def proximity_unavailable_summary(items: list[ProximityDisplayItem]) -> str | None:
    n = sum(1 for item in items if item.status == "unavailable")
    if n == 0:
        return None
    if n == 1:
        return "1 proximity criterion unavailable"
    return f"{n} proximity criteria unavailable"


def format_proximity_caption(items: list[ProximityDisplayItem]) -> str:
    """Compact joined caption for current Grid/Table. No '(some unknown)'."""
    if not items:
        return "—"
    parts: list[str] = []
    unavailable_texts: list[str] = []
    for item in items:
        if item.status == "available":
            if item.display_text:
                parts.append(item.display_text)
        else:
            unavailable_texts.append(item.unavailable_text or item.display_text)
    if len(unavailable_texts) == 1:
        parts.append(unavailable_texts[0])
    elif len(unavailable_texts) > 1:
        summary = proximity_unavailable_summary(items)
        if summary:
            parts.append(summary)
    return "; ".join(parts) if parts else "—"


def _format_proximity_display(proximity: dict | None) -> str:
    """Compatibility wrapper: structured proximity joined into one caption."""
    return format_proximity_caption(parse_proximity_display(proximity))


def listing_tag_labels(listing: dict) -> list[str]:
    """Freshness/open-house/price-drop labels; empty when none apply."""
    badges: list[str] = []
    age_hours = listing.get("listing_age_hours")
    if age_hours is not None:
        try:
            if float(age_hours) <= 48:
                badges.append("New")
        except (TypeError, ValueError):
            pass
    if listing.get("open_house"):
        badges.append("Open house")
    if listing.get("price_change_display"):
        badges.append("Reduced")
    return badges


def _format_tags(listing: dict) -> str:
    """Joined tags using the same labels as Grid; empty string when none apply."""
    return " · ".join(listing_tag_labels(listing))


def _format_days_on_market(listing: dict) -> str:
    """'Days on Market', approximated from listing_age_hours (parsed from the actor's
    relative freshness text, e.g. '18 hours ago') — the actor has no exact DOM field."""
    hours = _finite_or_none(listing.get("listing_age_hours"))
    if hours is None:
        return "—"
    return f"{round(hours / 24)}d"


def _format_bedrooms(listing: dict) -> str:
    """Bedroom count for display, preserving the source's den notation (e.g. '2 + 1')."""
    display = _plain_display_text(listing.get("bedrooms_display"), fallback="")
    if display:
        return display
    bedrooms = listing.get("bedrooms")
    if bedrooms is None or bedrooms == "":
        return "—"
    if _is_finite_number(bedrooms):
        n = float(bedrooms)
        if n == int(n):
            return str(int(n))
        return f"{n:g}"
    return _plain_display_text(bedrooms)


def format_property_basics(listing: dict) -> str:
    """Compact facts for Grid: '3 bd · 2 ba · 1,852 sq ft'. Omits missing segments."""
    parts: list[str] = []
    beds = _format_bedrooms(listing)
    if beds != "—":
        parts.append(f"{beds} bd")
    baths = format_count(listing.get("bathrooms"))
    if baths != "—":
        parts.append(f"{baths} ba")
    size = format_sqft(listing.get("sqft"))
    if size != "—":
        parts.append(size)
    return " · ".join(parts)


def proximity_card_lines(items: list[ProximityDisplayItem]) -> list[tuple[str, str]]:
    """One display line per proximity criterion for Grid. Never '(some unknown)'."""
    lines: list[tuple[str, str]] = []
    for item in items:
        if item.status == "available":
            if item.display_text:
                lines.append(("available", item.display_text))
            continue
        text = item.unavailable_text or item.display_text
        if text:
            lines.append(("unavailable", f"ⓘ {text}"))
    return lines


def _format_listing_price(listing: dict) -> str:
    """Human-readable price for Grid/Table.

    Prefer the numeric ``price`` field so scraped ``price_display`` cannot inject
    Markdown links into any Markdown render path. Fall back to plain display text.
    """
    price = listing.get("price")
    if price is not None:
        formatted = format_currency(price)
        if formatted != "—":
            return formatted
    return _plain_display_text(listing.get("price_display"))


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
    if not math.isfinite(value):
        return "—"
    if value >= 1_000_000:
        millions = value / 1_000_000
        formatted = f"{millions:.2f}".rstrip("0").rstrip(".")
        return f"${formatted}M"
    return f"${int(round(value)):,}"


def inject_results_css() -> None:
    """Scoped styles for search-result Grid and compact Match (theme-variable friendly)."""
    st.markdown(
        """
        <style>
        .rsa-match-compact {
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
            line-height: 1;
            font-family: inherit;
        }
        .rsa-match-compact svg { display: block; flex: 0 0 auto; }
        .rsa-match-compact-pct {
            font-size: 0.85rem;
            font-weight: 650;
            font-family: inherit;
        }
        .rsa-match-compact-label {
            font-size: 0.75rem;
            opacity: 0.75;
            font-family: inherit;
        }
        .rsa-results-header { margin-bottom: 0.35rem; min-width: 0; }
        .rsa-results-title { font-size: 1.35rem; font-weight: 700; line-height: 1.25; overflow-wrap: anywhere; }
        .rsa-results-meta { opacity: 0.72; font-size: 0.9rem; margin-top: 0.15rem; overflow-wrap: anywhere; }
        .rsa-card-photo {
            position: relative;
            width: 100%;
            border-radius: 0.55rem;
            overflow: hidden;
            background: rgba(128,128,128,0.12);
        }
        .rsa-card-photo img,
        .rsa-card-photo-img {
            display: block;
            width: 100%;
            aspect-ratio: 16 / 10;
            object-fit: cover;
        }
        .rsa-card-photo-fallback,
        .rsa-card-photo-fallback-link {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            aspect-ratio: 16 / 10;
            text-decoration: none;
            color: inherit;
            opacity: 0.7;
            font-size: 0.85rem;
        }
        .rsa-card-rank {
            position: absolute;
            top: 0.45rem;
            left: 0.45rem;
            z-index: 1;
            font-size: 0.75rem;
            font-weight: 650;
            letter-spacing: 0.02em;
            padding: 0.12rem 0.4rem;
            border-radius: 999px;
            background: rgba(0,0,0,0.55);
            color: #fff;
        }
        .rsa-card-price {
            font-size: 1.2rem;
            font-weight: 700;
            line-height: 1.25;
            font-family: inherit;
        }
        .rsa-card-match { display: flex; justify-content: flex-end; align-items: center; }
        .rsa-card-address { min-height: 2.55rem; margin-top: 0.15rem; }
        .rsa-card-street { font-weight: 600; line-height: 1.3; overflow-wrap: anywhere; }
        .rsa-card-street a { color: inherit; text-decoration: none; }
        .rsa-card-street a:hover { text-decoration: underline; }
        .rsa-card-locality { opacity: 0.7; font-size: 0.85rem; line-height: 1.35; margin-top: 0.1rem; overflow-wrap: anywhere; }
        .rsa-card-facts { min-height: 1.25rem; opacity: 0.82; font-size: 0.9rem; margin: 0.25rem 0 0.15rem; overflow-wrap: anywhere; }
        .rsa-card-tags { display: flex; flex-wrap: wrap; gap: 0.3rem; margin: 0.2rem 0 0.15rem; }
        .rsa-card-tag {
            font-size: 0.7rem;
            font-weight: 600;
            letter-spacing: 0.02em;
            opacity: 0.75;
            padding: 0.08rem 0.4rem;
            border: 1px solid rgba(128,128,128,0.3);
            border-radius: 999px;
            white-space: nowrap;
        }
        .rsa-card-prox { min-height: 3.1rem; margin: 0.35rem 0 0.15rem; font-size: 0.85rem; line-height: 1.4; overflow-wrap: anywhere; }
        .rsa-card-prox-ok { opacity: 0.9; }
        .rsa-card-prox-unavail { opacity: 0.72; }
        .rsa-card-photo, .rsa-card-photo img, .rsa-card-photo-img { max-width: 100%; }
        .rsa-card-price { overflow-wrap: anywhere; }
        .rsa-table-photo { width: 64px; max-width: 100%; }
        .rsa-table-photo-img {
            display: block;
            width: 64px;
            max-width: 100%;
            aspect-ratio: 16 / 10;
            object-fit: cover;
            border-radius: 0.3rem;
        }
        .rsa-table-photo-fallback {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 64px;
            aspect-ratio: 16 / 10;
            border-radius: 0.3rem;
            background: rgba(128,128,128,0.12);
            text-decoration: none;
            color: inherit;
            font-size: 0.7rem;
            opacity: 0.7;
        }
        .rsa-table-street { font-weight: 600; font-size: 0.82rem; line-height: 1.25; overflow-wrap: anywhere; word-break: break-word; }
        .rsa-table-street a { color: inherit; text-decoration: none; }
        .rsa-table-street a:hover { text-decoration: underline; }
        .rsa-table-locality { opacity: 0.68; font-size: 0.72rem; line-height: 1.25; margin-top: 0.05rem; overflow-wrap: anywhere; }
        .rsa-table-prox { font-size: 0.75rem; line-height: 1.3; overflow-wrap: anywhere; }
        .rsa-table-prox-ok { opacity: 0.9; }
        .rsa-table-prox-unavail { opacity: 0.72; }
        .rsa-table-num { font-variant-numeric: tabular-nums; font-size: 0.88rem; }
        .rsa-table-rule { border-top: 1px solid rgba(128,128,128,0.22); margin: 0.2rem 0; }
        .rsa-map-coverage { opacity: 0.72; font-size: 0.85rem; margin: 0.15rem 0 0.35rem; }
        .rsa-map-summary-rank { font-size: 0.75rem; font-weight: 650; opacity: 0.75; }
        .rsa-map-summary-price { font-weight: 700; font-size: 0.95rem; }
        .rsa-map-summary-street { font-size: 0.82rem; font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
        .rsa-map-summary-facts { font-size: 0.75rem; opacity: 0.75; overflow-wrap: anywhere; }
        .rsa-map-summary-price { overflow-wrap: anywhere; }
        </style>
        """,
        unsafe_allow_html=True,
    )


@dataclass(frozen=True)
class TableColumn:
    key: str
    header: str
    width: float
    help: str | None = None


def table_has_visible_tags(listings: list[dict]) -> bool:
    """True when at least one displayed listing has a New / Open house / Reduced tag."""
    return any(listing_tag_labels(item) for item in listings if isinstance(item, dict))


def table_column_schema(listings: list[dict]) -> list[TableColumn]:
    """Column headers/widths for the comparison table. Tags only when a result has one."""
    columns = [
        TableColumn("rank", "Rank", 0.42),
        TableColumn("photo", "Photo", 0.7),
        TableColumn("address", "Address", 1.7),
        TableColumn("type", "Type", 0.75),
        TableColumn("bed", "Bed", 0.42),
        TableColumn("bath", "Bath", 0.42),
        TableColumn("size", "Size", 0.85),
        TableColumn("price", "Price", 0.85),
        TableColumn(
            "dom",
            "DOM",
            0.48,
            "Approximate days since listing publication",
        ),
        TableColumn("match", "Match", 0.8),
    ]
    if table_has_visible_tags(listings):
        columns.append(TableColumn("tags", "Tags", 0.85))
    columns.extend(
        [
            TableColumn("proximity", "Proximity", 1.35),
            TableColumn("analyze", "Analyze", 0.7),
        ]
    )
    return columns


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
        rows.append({
            "rank": listing_rank(listing, i),
            "photo": safe_http_url(listing.get("photo_url")) or "",
            "address": _plain_display_text(listing.get("address")),
            "type": _plain_display_text(listing.get("house_category")),
            "bed": _format_bedrooms(listing),
            "bath": format_count(listing.get("bathrooms")),
            "size": format_sqft(listing.get("sqft")),
            "price": _format_listing_price(listing),
            "days_on_market": _format_days_on_market(listing),
            "match_score": _format_match_score(listing),
            "tags": _format_tags(listing),
            "Proximity": format_proximity_caption(
                parse_proximity_display(listing.get("proximity"))
            ),
            "URL": safe_http_url(listing.get("url")) or "",
        })
    return rows


def _render_table_photo(photo_url: str, listing_url: str) -> None:
    """Compact 64px 16:10 thumbnail. Safe HTTP(S) only."""
    safe_listing = safe_http_url(listing_url) or ""
    safe_photo = safe_http_url(photo_url) or ""
    if safe_photo:
        img = f'<img src="{html.escape(safe_photo)}" alt="" class="rsa-table-photo-img">'
        body = (
            f'<a href="{html.escape(safe_listing)}" target="_blank" rel="noopener">{img}</a>'
            if safe_listing
            else img
        )
    elif safe_listing:
        body = (
            f'<a href="{html.escape(safe_listing)}" target="_blank" rel="noopener" '
            f'class="rsa-table-photo-fallback">View</a>'
        )
    else:
        body = '<div class="rsa-table-photo-fallback" aria-hidden="true"></div>'
    st.markdown(f'<div class="rsa-table-photo">{body}</div>', unsafe_allow_html=True)


def _render_table_address(listing: dict) -> None:
    headline, locality = listing_address_parts(listing)
    safe_listing = safe_http_url(listing.get("url") or "") or ""
    street = html.escape(headline or "—")
    if safe_listing:
        street_html = (
            f'<a href="{html.escape(safe_listing)}" target="_blank" rel="noopener">{street}</a>'
        )
    else:
        street_html = street
    loc_html = (
        f'<div class="rsa-table-locality">{html.escape(locality)}</div>' if locality else ""
    )
    st.markdown(
        f'<div class="rsa-table-address"><div class="rsa-table-street">{street_html}</div>'
        f"{loc_html}</div>",
        unsafe_allow_html=True,
    )


def _render_table_proximity(listing: dict) -> None:
    lines = proximity_card_lines(parse_proximity_display(listing.get("proximity")))
    if not lines:
        st.markdown('<div class="rsa-table-prox">—</div>', unsafe_allow_html=True)
        return
    blocks = []
    for kind, text in lines:
        cls = "rsa-table-prox-ok" if kind == "available" else "rsa-table-prox-unavail"
        blocks.append(f'<div class="{cls}">{html.escape(text)}</div>')
    st.markdown(
        f'<div class="rsa-table-prox">{"".join(blocks)}</div>',
        unsafe_allow_html=True,
    )


def _render_table_cell(column_key: str, listing: dict, index: int) -> None:
    if column_key == "rank":
        st.markdown(
            f'<div class="rsa-table-num">{html.escape(str(listing_rank(listing, index)))}</div>',
            unsafe_allow_html=True,
        )
        return
    if column_key == "photo":
        _render_table_photo(listing.get("photo_url") or "", listing.get("url") or "")
        return
    if column_key == "address":
        _render_table_address(listing)
        return
    if column_key == "type":
        st.write(_plain_display_text(listing.get("house_category")))
        return
    if column_key == "bed":
        st.write(_format_bedrooms(listing))
        return
    if column_key == "bath":
        st.write(format_count(listing.get("bathrooms")))
        return
    if column_key == "size":
        st.markdown(
            f'<div class="rsa-table-num">{html.escape(format_sqft(listing.get("sqft")))}</div>',
            unsafe_allow_html=True,
        )
        return
    if column_key == "price":
        st.markdown(
            f'<div class="rsa-table-num">{html.escape(_format_listing_price(listing))}</div>',
            unsafe_allow_html=True,
        )
        return
    if column_key == "dom":
        st.write(_format_days_on_market(listing))
        return
    if column_key == "match":
        render_compact_match_score(listing, size=28, show_label=False)
        return
    if column_key == "tags":
        labels = listing_tag_labels(listing)
        st.caption(" · ".join(labels) if labels else "—")
        return
    if column_key == "proximity":
        _render_table_proximity(listing)
        return
    if column_key == "analyze":
        if st.button("Analyze", key=_analyze_button_key(listing, index)):
            request_listing_analysis(listing)


def _render_results_table(listings: list[dict]) -> None:
    """Dense comparison table. Uses listing rank and shared Match/proximity helpers."""
    if not listings:
        return
    columns = table_column_schema(listings)
    widths = [col.width for col in columns]
    header_cols = st.columns(widths)
    for col, spec in zip(header_cols, columns):
        with col:
            if spec.help:
                st.caption(spec.header, help=spec.help)
            else:
                st.caption(spec.header)
    st.markdown('<div class="rsa-table-rule"></div>', unsafe_allow_html=True)
    for i, listing in enumerate(listings):
        row_cols = st.columns(widths)
        for col, spec in zip(row_cols, columns):
            with col:
                _render_table_cell(spec.key, listing, i)
        if i < len(listings) - 1:
            st.markdown('<div class="rsa-table-rule"></div>', unsafe_allow_html=True)


def _render_grid_photo(photo_url: str, listing_url: str, rank: Any) -> None:
    """Uniform 16:10 card image with a secondary rank badge. Safe HTTP(S) only."""
    safe_listing = safe_http_url(listing_url) or ""
    safe_photo = safe_http_url(photo_url) or ""
    rank_html = f'<span class="rsa-card-rank">#{html.escape(str(rank))}</span>'
    if safe_photo:
        img = f'<img src="{html.escape(safe_photo)}" alt="" class="rsa-card-photo-img">'
        body = (
            f'<a href="{html.escape(safe_listing)}" target="_blank" rel="noopener">{img}</a>'
            if safe_listing
            else img
        )
    elif safe_listing:
        body = (
            f'<a href="{html.escape(safe_listing)}" target="_blank" rel="noopener" '
            f'class="rsa-card-photo-fallback-link">View listing</a>'
        )
    else:
        body = '<div class="rsa-card-photo-fallback" aria-hidden="true"></div>'
    st.markdown(
        f'<div class="rsa-card-photo">{body}{rank_html}</div>',
        unsafe_allow_html=True,
    )


def _render_grid_address(headline: str, locality: str, listing_url: str) -> None:
    """Street as primary (linked when safe); locality muted."""
    safe_listing = safe_http_url(listing_url) or ""
    street = html.escape(headline or "—")
    if safe_listing:
        street_html = (
            f'<a href="{html.escape(safe_listing)}" target="_blank" rel="noopener">{street}</a>'
        )
    else:
        street_html = street
    loc_html = (
        f'<div class="rsa-card-locality">{html.escape(locality)}</div>' if locality else ""
    )
    st.markdown(
        f'<div class="rsa-card-address"><div class="rsa-card-street">{street_html}</div>'
        f"{loc_html}</div>",
        unsafe_allow_html=True,
    )


def _render_grid_card(listing: dict, index: int) -> None:
    url = listing.get("url") or ""
    photo_url = listing.get("photo_url") or ""
    rank = listing_rank(listing, index)
    headline, locality = listing_address_parts(listing)
    facts = format_property_basics(listing)
    tags = listing_tag_labels(listing)
    prox_lines = proximity_card_lines(parse_proximity_display(listing.get("proximity")))
    price = html.escape(_format_listing_price(listing))
    with st.container(border=True):
        _render_grid_photo(photo_url, url, rank)
        price_col, match_col = st.columns([1.35, 1])
        with price_col:
            st.markdown(f'<div class="rsa-card-price">{price}</div>', unsafe_allow_html=True)
        with match_col:
            render_compact_match_score(listing, size=32, show_label=True)
        _render_grid_address(headline, locality, url)
        if facts:
            st.markdown(
                f'<div class="rsa-card-facts">{html.escape(facts)}</div>',
                unsafe_allow_html=True,
            )
        if tags:
            chips = "".join(
                f'<span class="rsa-card-tag">{html.escape(tag)}</span>' for tag in tags
            )
            st.markdown(f'<div class="rsa-card-tags">{chips}</div>', unsafe_allow_html=True)
        st.divider()
        if prox_lines:
            blocks = []
            for kind, text in prox_lines:
                cls = "rsa-card-prox-ok" if kind == "available" else "rsa-card-prox-unavail"
                blocks.append(f'<div class="{cls}">{html.escape(text)}</div>')
            st.markdown(
                f'<div class="rsa-card-prox">{"".join(blocks)}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div class="rsa-card-prox"></div>', unsafe_allow_html=True)
        if st.button("Analyze", type="primary", key=_analyze_button_key(listing, index)):
            request_listing_analysis(listing)


def _render_results_grid(listings: list[dict]) -> None:
    """Render search results as a 3-column Grid. Photo and street open the listing."""
    if not listings:
        return
    for row_start in range(0, len(listings), _CARDS_PER_ROW):
        cols = st.columns(_CARDS_PER_ROW)
        chunk = listings[row_start : row_start + _CARDS_PER_ROW]
        for offset, listing in enumerate(chunk):
            with cols[offset]:
                _render_grid_card(listing, row_start + offset)


def _render_results_header(listings: list[dict]) -> None:
    """Shared Search results header with count and Grid/Table/Map selector."""
    count = results_count_label(len(listings))
    sort_caption = ordered_by_caption(st.session_state.get("last_sort_by"))
    meta = count if not sort_caption else f"{count} · {sort_caption}"
    left, right = st.columns([1.4, 1.2])
    with left:
        st.markdown(
            f'<div class="rsa-results-header">'
            f'<div class="rsa-results-title">Search results</div>'
            f'<div class="rsa-results-meta">{html.escape(meta)}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
    with right:
        st.segmented_control(
            "Results view",
            options=list(WIDGET_RESULTS_VIEWS),
            format_func=lambda x: _VIEW_LABELS.get(x, x.title()),
            key="results_view",
            label_visibility="collapsed",
        )


def _listings_cache_key(listings: list[dict]) -> str:
    """Stable JSON string for listings, used as cache key. Lists/dicts must be hashable for st.cache_data."""
    return json.dumps(listings, sort_keys=True, default=str)


def _map_point_label(listing: dict, index: int, label_mode: str) -> str:
    if label_mode == "price":
        return _format_map_price_label(listing)
    if label_mode == "match":
        pct, _color = match_score_display(listing)
        return "—" if pct is None else f"{pct}%"
    return str(listing_rank(listing, index))


def _folium_popup_html(point: dict) -> str:
    street = html.escape(str(point.get("street") or "Listing"))
    price = html.escape(str(point.get("price") or "—"))
    match_pct = point.get("match_pct")
    match_txt = "—" if match_pct is None else f"{int(match_pct)}%"
    facts = html.escape(str(point.get("facts") or ""))
    url = point.get("url") or ""
    parts = [f"<strong>{street}</strong>", price, f"Match {html.escape(match_txt)}"]
    if facts:
        parts.append(facts)
    if url:
        parts.append(
            f'<a href="{html.escape(url)}" target="_blank" rel="noopener">View listing</a>'
        )
    return "<br>".join(parts)


def _folium_marker_icon(point: dict, label_mode: str):
    """DivIcon: price rectangle, match pill (shared score color), or rank circle."""
    url_escaped = html.escape(point.get("url") or "#")
    label_escaped = html.escape(str(point.get("label") or ""))
    if label_mode == "price":
        marker_html = (
            '<div style="font-size:12px;font-weight:normal;color:white;text-align:center;'
            "line-height:20px;padding:1px 6px;min-width:54px;height:22px;border-radius:4px;"
            f'background-color:{_MAP_MARKER_BLUE};border:2px solid white;white-space:nowrap;">'
            f'<a href="{url_escaped}" target="_blank" rel="noopener" '
            f'style="color:white;text-decoration:none;">{label_escaped}</a></div>'
        )
        return folium.DivIcon(icon_size=(72, 26), icon_anchor=(36, 13), html=marker_html)
    if label_mode == "match":
        bg = html.escape(str(point.get("match_color") or "#888888"))
        marker_html = (
            '<div style="font-size:12px;font-weight:650;color:white;text-align:center;'
            "line-height:20px;padding:1px 7px;min-width:40px;height:22px;border-radius:999px;"
            f'background-color:{bg};border:2px solid white;white-space:nowrap;">'
            f'<a href="{url_escaped}" target="_blank" rel="noopener" '
            f'style="color:white;text-decoration:none;">{label_escaped}</a></div>'
        )
        return folium.DivIcon(icon_size=(56, 26), icon_anchor=(28, 13), html=marker_html)
    marker_html = (
        '<div style="font-size:14pt;font-weight:bold;color:white;text-align:center;'
        "line-height:30px;width:30px;height:30px;border-radius:50%;"
        f'background-color:{_MAP_MARKER_BLUE};border:2px solid white;">'
        f'<a href="{url_escaped}" target="_blank" rel="noopener" '
        f'style="color:white;text-decoration:none;">{label_escaped}</a></div>'
    )
    return folium.DivIcon(icon_size=(32, 32), icon_anchor=(16, 16), html=marker_html)


def _add_folium_markers(m, map_points: list[dict], label_mode: str) -> None:
    for pt in map_points:
        folium.Marker(
            location=[pt["lat"], pt["lon"]],
            icon=_folium_marker_icon(pt, label_mode),
            popup=folium.Popup(_folium_popup_html(pt), max_width=240),
        ).add_to(m)


def _make_folium_map(
    map_points: list[dict],
    center_lat: float,
    center_lon: float,
    label_mode: str,
):
    """Folium map fitted to result markers. One point uses a local zoom."""
    if len(map_points) == 1:
        m = folium.Map(location=[center_lat, center_lon], zoom_start=14)
    else:
        m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
        m.fit_bounds([[pt["lat"], pt["lon"]] for pt in map_points], padding=(28, 28))
    _add_folium_markers(m, map_points, label_mode)
    return m


@st.cache_data(show_spinner=False)
def _get_map_html_cached(listings_json: str, label_mode: str = "rank") -> str | None:
    """Build Folium map HTML from listings. Cached by listings content and label_mode."""
    if folium is None:
        return None
    listings = json.loads(listings_json) if listings_json else []
    map_points, center_lat, center_lon = _build_map_data(listings, label_mode=label_mode)
    if not map_points or center_lat is None or center_lon is None:
        return None
    m = _make_folium_map(map_points, center_lat, center_lon, label_mode)
    return m._repr_html_()


def _build_map_data(
    listings: list[dict], label_mode: str = "rank"
) -> tuple[list[dict], float | None, float | None]:
    """Build map points for listings with valid coordinates.

    Returns (map_points, center_lat, center_lon). Center is None if no points.
    Rank labels use listing['rank'], not current list position. Price uses the
    compact map formatter. Match uses match_score with semantic_score fallback.
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
        headline, locality = listing_address_parts(listing)
        pct, color = match_score_display(listing)
        points.append(
            {
                "id": listing.get("id"),
                "rank": listing_rank(listing, i),
                "lat": lat,
                "lon": lon,
                "label": _map_point_label(listing, i, label_mode),
                "url": safe_http_url(listing.get("url")) or "",
                "price": _format_listing_price(listing),
                "match_pct": pct,
                "match_color": color,
                "street": headline,
                "locality": locality,
                "facts": format_property_basics(listing),
                "photo_url": safe_http_url(listing.get("photo_url")) or "",
            }
        )
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
    """Render Folium (fitted bounds) or PyDeck fallback. Match labels must not crash."""
    if folium is not None:
        m = _make_folium_map(map_points, center_lat, center_lon, label_mode)
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
            zoom=11 if len(map_points) > 1 else 13,
            pitch=0,
        )
        st.pydeck_chart(
            pdk.Deck(
                layers=[scatter, text],
                initial_view_state=view_state,
            ),
            width="stretch",
            height=400,
        )
        return
    st.caption("Map unavailable: install folium (recommended) or pydeck to show results on a map.")


def _render_map_summaries(listings: list[dict]) -> None:
    """Compact identity strip under the map. Not a second Grid."""
    if not listings:
        return
    for row_start in range(0, len(listings), _MAP_SUMMARY_PER_ROW):
        cols = st.columns(_MAP_SUMMARY_PER_ROW)
        chunk = listings[row_start : row_start + _MAP_SUMMARY_PER_ROW]
        for offset, listing in enumerate(chunk):
            with cols[offset]:
                i = row_start + offset
                rank = listing_rank(listing, i)
                headline, _locality = listing_address_parts(listing)
                facts = format_property_basics(listing)
                st.markdown(
                    f'<div class="rsa-map-summary-rank">#{html.escape(str(rank))}</div>',
                    unsafe_allow_html=True,
                )
                _render_table_photo(listing.get("photo_url") or "", listing.get("url") or "")
                st.markdown(
                    f'<div class="rsa-map-summary-price">'
                    f"{html.escape(_format_listing_price(listing))}</div>",
                    unsafe_allow_html=True,
                )
                render_compact_match_score(listing, size=24, show_label=False)
                st.markdown(
                    f'<div class="rsa-map-summary-street">{html.escape(headline)}</div>',
                    unsafe_allow_html=True,
                )
                if facts:
                    st.markdown(
                        f'<div class="rsa-map-summary-facts">{html.escape(facts)}</div>',
                        unsafe_allow_html=True,
                    )


def _render_map_panel(listings: list[dict]) -> None:
    """Map as the active results view: labels, coverage, Folium/PyDeck, summaries."""
    prepare_map_label_widget_state(st.session_state)
    st.segmented_control(
        "Map labels",
        options=list(WIDGET_MAP_LABEL_MODES),
        format_func=lambda x: _MAP_LABEL_LABELS.get(x, x.title()),
        key="map_label_mode",
    )
    label_mode = current_map_label_mode(st.session_state)
    map_points, center_lat, center_lon = _build_map_data(listings, label_mode=label_mode)
    st.markdown(
        f'<div class="rsa-map-coverage">'
        f"{html.escape(format_map_coverage(len(listings), len(map_points)))}</div>",
        unsafe_allow_html=True,
    )
    if map_points and center_lat is not None and center_lon is not None:
        if folium is not None:
            map_html = _get_map_html_cached(_listings_cache_key(listings), label_mode)
            if map_html:
                st.components.v1.html(map_html, height=400, scrolling=False)
            else:
                _render_results_map(map_points, center_lat, center_lon, label_mode)
        else:
            _render_results_map(map_points, center_lat, center_lon, label_mode)
    else:
        st.caption("No map: addresses have no coordinates.")
    _render_map_summaries(listings)


def render_search_results(listings: list[dict]) -> None:
    """Render the shared header and exactly one of Grid, Table, or Map."""
    if not listings:
        return
    inject_results_css()
    prepare_results_widget_state(st.session_state)
    _render_results_header(listings)
    view = current_results_view(st.session_state)
    if view == "table":
        _render_results_table(listings)
    elif view == "map":
        _render_map_panel(listings)
    else:
        _render_results_grid(listings)
