"""Pre-search landing page and Chat empty-state presentation."""

from __future__ import annotations

import html
import re
from typing import Any, Callable, Mapping, NamedTuple, Sequence

import streamlit as st

from rental_search_agent.display_format import (
    format_count,
    format_currency,
    format_sqft,
    parse_budget_input,
)
from rental_search_agent.preference_apply import first_search_required_warnings

# ---------------------------------------------------------------------------
# Landing copy and Chat starter prompts
# ---------------------------------------------------------------------------

LANDING_HERO_LEAD = (
    "Set your preferences or search naturally in chat, then compare and analyze "
    "your best matches."
)

HOW_IT_WORKS_HEADING = "How it works"
HOW_IT_WORKS_SUBTITLE = (
    "A simpler way to find, compare, and understand properties."
)

SAVED_PREFS_HEADING_READY = "Ready to search with your saved preferences"
SAVED_PREFS_HEADING_MISSING_LOCATION = (
    "Add a location to search with your saved preferences"
)
SAVED_PREFS_HEADING_MISSING_BEDS = (
    "Add a minimum bedroom count to search with your saved preferences"
)
SAVED_PREFS_HEADING_MISSING_BOTH = (
    "Add a location and minimum bedrooms to get started"
)


class HowItWorksStep(NamedTuple):
    """One stage in the landing How-it-works workflow."""

    number: str
    icon: str
    title: str
    copy: str
    chips: tuple[str, ...]


HOW_IT_WORKS_STEPS: tuple[HowItWorksStep, ...] = (
    HowItWorksStep(
        number="1",
        icon="prefs",
        title="Set preferences",
        copy=(
            "Define the requirements that matter to you or use your saved "
            "preferences."
        ),
        chips=("Location", "Budget", "Bedrooms", "Amenities", "Proximity"),
    ),
    HowItWorksStep(
        number="2",
        icon="compare",
        title="Compare matches",
        copy=(
            "See matched properties in Grid, Table, or Map view and compare "
            "key details at a glance."
        ),
        chips=("Grid", "Table", "Map", "Match score"),
    ),
    HowItWorksStep(
        number="3",
        icon="analyze",
        title="Analyze a property",
        copy=(
            "Open Analyze to understand why a property matches, where the "
            "evidence comes from, and what may still be missing."
        ),
        chips=("Checklist", "Score breakdown", "Highlights", "Open questions"),
    ),
)

CHAT_STARTER_PROMPTS: tuple[str, ...] = (
    "2 bed condo in Vancouver under $3,000",
    "2 bed apartment in Burnaby with parking and balcony",
    "2 bed condo in Vancouver within 30 minutes of downtown, "
    "5 minutes walk from a public transit station",
)

_LISTING_TYPE_LABELS = {
    "for_sale": "Buy",
    "sale": "Buy",
    "buy": "Buy",
    "for_rent": "Rent",
    "rent": "Rent",
    "rental": "Rent",
}

_VISIBLE_CHAT_ROLES = frozenset({"user", "assistant"})

_SVG_ICONS = {
    "chat": (
        '<svg class="rsa-landing-svg" width="28" height="28" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">'
        '<path d="M21 12a8.5 8.5 0 0 1-8.5 8.5H7l-4 3V12A8.5 8.5 0 1 1 21 12z"/>'
        "</svg>"
    ),
    "location": (
        '<svg class="rsa-landing-svg" width="14" height="14" viewBox="0 0 24 24" '
        'fill="currentColor" aria-hidden="true">'
        '<path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 '
        '9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z"/>'
        "</svg>"
    ),
    "home": (
        '<svg class="rsa-landing-svg" width="14" height="14" viewBox="0 0 24 24" '
        'fill="currentColor" aria-hidden="true">'
        '<path d="M12 3l9 8h-3v9h-5v-6H11v6H6v-9H3l9-8z"/>'
        "</svg>"
    ),
    "budget": (
        '<svg class="rsa-landing-svg" width="14" height="14" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
        '<circle cx="12" cy="12" r="8"/>'
        '<path d="M12 7v10M9.5 9.5c.6-1 1.6-1.5 2.5-1.5 1.4 0 2.5.8 2.5 2s-1.1 2-2.5 '
        '2h-1c-1.4 0-2.5.8-2.5 2s1.1 2 2.5 2c.9 0 1.9-.5 2.5-1.5"/>'
        "</svg>"
    ),
    "beds": (
        '<svg class="rsa-landing-svg" width="14" height="14" viewBox="0 0 24 24" '
        'fill="currentColor" aria-hidden="true">'
        '<path d="M4 11V7.5A2.5 2.5 0 0 1 6.5 5h3A2.5 2.5 0 0 1 12 7.5V11h8v7h-2v-3H6v3H4v-7z"/>'
        "</svg>"
    ),
    "baths": (
        '<svg class="rsa-landing-svg" width="14" height="14" viewBox="0 0 24 24" '
        'fill="currentColor" aria-hidden="true">'
        '<path d="M7 4a2 2 0 0 1 2 2v5h11v2.5A4.5 4.5 0 0 1 15.5 18H7v2H5V6a2 2 0 0 1 2-2z"/>'
        "</svg>"
    ),
    "sqft": (
        '<svg class="rsa-landing-svg" width="14" height="14" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
        '<path d="M4 9V4h5M20 15v5h-5M20 9V4h-5M4 15v5h5"/>'
        "</svg>"
    ),
    "prefs": (
        '<svg class="rsa-landing-svg" width="18" height="18" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
        '<path d="M4 7h10M4 17h10M18 5v4M18 15v4"/>'
        '<circle cx="16" cy="7" r="2.2"/><circle cx="16" cy="17" r="2.2"/>'
        "</svg>"
    ),
    "compare": (
        '<svg class="rsa-landing-svg" width="18" height="18" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
        '<path d="M4 6h16M4 12h16M4 18h10"/>'
        "</svg>"
    ),
    "analyze": (
        '<svg class="rsa-landing-svg" width="18" height="18" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
        '<path d="M4 19V5M4 19h16"/>'
        '<path d="M8 15l3.5-4 3 2.5L18 8"/>'
        "</svg>"
    ),
}

_CHIP_ICON_ALIASES = {
    "listing_type": "home",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def format_landing_budget_chip(value: Any) -> str | None:
    """Compact budget chip such as ``≤ $1M`` or ``≤ $2,800``. Never raw floats."""
    parsed = parse_budget_input(value)
    if parsed is None:
        return None
    try:
        n = float(parsed)
    except (TypeError, ValueError):
        return None
    if n >= 1_000_000 and n == int(n) and int(n) % 1_000_000 == 0:
        return f"≤ ${int(n) // 1_000_000}M"
    formatted = format_currency(n, unavailable="")
    if not formatted:
        return None
    return f"≤ {formatted}"


def format_landing_beds_chip(min_bedrooms: Any, max_bedrooms: Any = None) -> str | None:
    mn = format_count(min_bedrooms, unavailable="")
    mx = format_count(max_bedrooms, unavailable="")
    if mn and mx and mn != mx:
        return f"{mn}–{mx} beds"
    if mn:
        return f"{mn} beds"
    if mx:
        return f"{mx} beds"
    return None


def format_landing_baths_chip(min_bathrooms: Any) -> str | None:
    n = format_count(min_bathrooms, unavailable="")
    if not n:
        return None
    return f"{n} baths"


def format_landing_sqft_chip(min_sqft: Any) -> str | None:
    formatted = format_sqft(min_sqft, unavailable="")
    if not formatted:
        return None
    return f"≥ {formatted}"


def landing_preference_chips(prefs: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """Return ``(kind, label)`` chips from saved preferences. Empty values omitted."""
    prefs = prefs or {}
    chips: list[tuple[str, str]] = []
    location = str(prefs.get("location") or "").strip()
    if location:
        chips.append(("location", location))
    listing_type = str(prefs.get("listing_type") or "").strip().lower()
    type_label = _LISTING_TYPE_LABELS.get(listing_type)
    if type_label:
        chips.append(("listing_type", type_label))
    budget = format_landing_budget_chip(prefs.get("budget_max"))
    if budget:
        chips.append(("budget", budget))
    beds = format_landing_beds_chip(prefs.get("min_bedrooms"), prefs.get("max_bedrooms"))
    if beds:
        chips.append(("beds", beds))
    baths = format_landing_baths_chip(prefs.get("min_bathrooms"))
    if baths:
        chips.append(("baths", baths))
    sqft = format_landing_sqft_chip(prefs.get("min_sqft"))
    if sqft:
        chips.append(("sqft", sqft))
    return chips


def saved_preferences_ready_for_search(prefs: Mapping[str, Any] | None) -> bool:
    """True when location and bedroom minimum are present (same rules as sidebar Search)."""
    return not first_search_required_warnings(prefs)


def missing_required_search_fields(
    prefs: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Required first-search fields that are empty: ``location`` and/or ``min_bedrooms``."""
    missing: list[str] = []
    prefs = prefs or {}
    if not str(prefs.get("location") or "").strip():
        missing.append("location")
    if not str(prefs.get("min_bedrooms") or "").strip():
        missing.append("min_bedrooms")
    return tuple(missing)


def landing_saved_prefs_heading(prefs: Mapping[str, Any] | None) -> str:
    """Section title for the saved-preferences summary, based on missing required fields."""
    missing = missing_required_search_fields(prefs)
    if missing == ("location", "min_bedrooms"):
        return SAVED_PREFS_HEADING_MISSING_BOTH
    if missing == ("location",):
        return SAVED_PREFS_HEADING_MISSING_LOCATION
    if missing == ("min_bedrooms",):
        return SAVED_PREFS_HEADING_MISSING_BEDS
    return SAVED_PREFS_HEADING_READY


def search_has_run(
    *,
    display_source: Any = None,
    search_master: Any = None,
    last_filters: Any = None,
) -> bool:
    """True after a legitimate search executed, including a zero-result search."""
    if display_source is not None:
        return True
    if search_master:
        return True
    if last_filters:
        return True
    return False


def center_panel_kind(
    *,
    listings: Sequence[Any] | None,
    display_source: Any = None,
    search_master: Any = None,
    last_filters: Any = None,
) -> str:
    """``results`` | ``zero_results`` | ``landing`` for the main column."""
    if listings:
        return "results"
    if search_has_run(
        display_source=display_source,
        search_master=search_master,
        last_filters=last_filters,
    ):
        return "zero_results"
    return "landing"


def has_visible_chat_history(messages: Sequence[Mapping[str, Any]] | None) -> bool:
    """True when a user or non-empty assistant message exists (system/tool do not count)."""
    for msg in messages or []:
        role = msg.get("role")
        if role not in _VISIBLE_CHAT_ROLES:
            continue
        if role == "user":
            return True
        if str(msg.get("content") or "").strip():
            return True
    return False


def should_render_chat_empty_state(
    messages: Sequence[Mapping[str, Any]] | None,
    *,
    pending_ask: Any = None,
    pending_chat_prompt: Any = None,
) -> bool:
    """True only before a conversation starts (no history, ask form, or queued prompt)."""
    if pending_ask is not None:
        return False
    if pending_chat_prompt:
        return False
    return not has_visible_chat_history(messages)


def chat_starter_has_required_criteria(prompt: str) -> bool:
    """Every Chat starter must include a bedroom count and a city."""
    text = prompt or ""
    has_beds = bool(re.search(r"\d+\s*-?\s*bed", text, flags=re.IGNORECASE))
    has_city = bool(re.search(r"\b(vancouver|burnaby)\b", text, flags=re.IGNORECASE))
    return has_beds and has_city


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def inject_landing_css() -> None:
    """Scoped landing + chat-empty styles. Hidden via existing rsa_hidden_* rule."""
    with st.container(key="rsa_hidden_css_landing"):
        st.markdown(
            """
            <style>
            [class*="st-key-rsa_landing_panel"] {
                border: 1px solid rgba(128, 128, 128, 0.28) !important;
                border-radius: 16px !important;
                padding: 1.6rem 1.8rem 1.1rem !important;
                max-width: 56rem;
                margin: 0.35rem auto 1.25rem !important;
            }
            .rsa-landing-hero { text-align: center; padding: 0.4rem 0.5rem 0.15rem; }
            .rsa-landing-eyebrow {
                font-size: 0.72rem; font-weight: 650; letter-spacing: 0.12em;
                text-transform: uppercase; opacity: 0.55; margin-bottom: 0.45rem;
            }
            .rsa-landing-title {
                font-size: clamp(1.7rem, 3.2vw, 2.35rem); font-weight: 700;
                letter-spacing: -0.02em; line-height: 1.15; margin: 0 0 0.55rem;
            }
            .rsa-landing-lead {
                font-size: 0.98rem; opacity: 0.72; line-height: 1.45;
                max-width: 42rem; margin: 0 auto 0.35rem;
            }
            [class*="st-key-rsa_landing_hero_actions"] {
                max-width: 34rem; margin: 0.55rem auto 0.35rem !important;
            }
            [class*="st-key-rsa_landing_search"] button,
            [class*="st-key-rsa_landing_ask"] button {
                border-radius: 999px !important;
                height: 2.55rem !important;
                min-height: 2.55rem !important;
            }
            .rsa-landing-section-title {
                font-size: 1.02rem; font-weight: 650; margin: 1.15rem 0 0.25rem;
            }
            .rsa-landing-chip-row {
                display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.35rem 0 0.2rem;
            }
            .rsa-landing-chip {
                display: inline-flex; align-items: center; gap: 0.35rem;
                font-size: 0.82rem; font-weight: 550;
                border: 1px solid rgba(128,128,128,0.32); border-radius: 999px;
                padding: 0.22rem 0.7rem; opacity: 0.92; max-width: 100%;
            }
            .rsa-landing-chip .rsa-landing-svg { flex-shrink: 0; opacity: 0.75; }
            [class*="st-key-rsa_chat_starter_"] button::after {
                content: "›";
                position: absolute;
                right: 0.7rem;
                top: 50%;
                transform: translateY(-50%);
                font-size: 1.2rem;
                opacity: 0.45;
                pointer-events: none;
            }
            .rsa-landing-how-heading {
                font-size: 1.2rem; font-weight: 700; margin: 1.45rem 0 0.25rem;
            }
            .rsa-landing-how-sub {
                font-size: 0.9rem; opacity: 0.65; line-height: 1.4; margin: 0 0 0.75rem;
            }
            .rsa-landing-how {
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 0.9rem;
                margin: 0.15rem 0 0.35rem;
                align-items: stretch;
            }
            .rsa-landing-how-card {
                display: flex; flex-direction: column; min-width: 0;
                box-sizing: border-box;
                border: 1px solid rgba(128,128,128,0.28);
                border-radius: 14px;
                padding: 1.15rem 1.1rem 1rem;
                background: rgba(128,128,128,0.045);
            }
            .rsa-landing-how-top {
                display: flex; align-items: center; justify-content: space-between;
                margin-bottom: 0.85rem;
            }
            .rsa-landing-how-top .rsa-landing-svg {
                width: 20px; height: 20px; opacity: 0.7; flex-shrink: 0;
            }
            .rsa-landing-how-num {
                display: inline-flex; align-items: center; justify-content: center;
                width: 1.7rem; height: 1.7rem; border-radius: 50%;
                background: var(--primary-color, #ff4b4b);
                color: #fff;
                font-size: 0.78rem; font-weight: 700; flex-shrink: 0;
                line-height: 1;
            }
            .rsa-landing-how-title {
                font-weight: 650; font-size: 1.05rem; line-height: 1.25;
                margin: 0 0 0.45rem;
            }
            .rsa-landing-how-copy {
                font-size: 0.88rem; opacity: 0.68; line-height: 1.45;
                margin: 0 0 0.95rem;
            }
            .rsa-landing-feature-chips {
                display: flex; flex-wrap: wrap; gap: 0.35rem;
                margin-top: auto;
            }
            .rsa-landing-feature-chip {
                display: inline-flex; align-items: center;
                font-size: 0.75rem; font-weight: 500;
                border: 1px solid rgba(128,128,128,0.32);
                border-radius: 999px;
                padding: 0.18rem 0.6rem;
                opacity: 0.82;
                pointer-events: none;
                max-width: 100%;
            }
            .rsa-landing-footer {
                text-align: center; font-size: 0.8rem; opacity: 0.45;
                margin: 1.1rem 0 0.15rem;
            }
            .rsa-landing-zero {
                text-align: center; padding: 2.4rem 1rem 2rem;
                max-width: 32rem; margin: 1.5rem auto;
            }
            .rsa-landing-zero p { margin: 0.25rem 0; line-height: 1.45; }
            .rsa-landing-zero p + p { opacity: 0.7; font-size: 0.95rem; }
            .rsa-chat-empty {
                display: flex; flex-direction: column; align-items: center;
                text-align: center; padding: 1.1rem 0.15rem 0.65rem;
            }
            .rsa-chat-empty-icon {
                width: 2.6rem; height: 2.6rem; border-radius: 50%;
                border: 1px solid rgba(128,128,128,0.3);
                display: inline-flex; align-items: center; justify-content: center;
                opacity: 0.7; margin-bottom: 0.7rem;
            }
            .rsa-chat-empty-title {
                font-size: 1.05rem; font-weight: 650; margin: 0 0 0.35rem;
            }
            .rsa-chat-empty-lead {
                font-size: 0.86rem; opacity: 0.65; line-height: 1.4;
                margin: 0 0 0.85rem; max-width: 22rem;
            }
            [class*="st-key-rsa_chat_starters"] { width: 100% !important; }
            [class*="st-key-rsa_chat_starter_"] button {
                width: 100% !important;
                border-radius: 10px !important;
                height: auto !important;
                min-height: 2.7rem !important;
                white-space: normal !important;
                overflow-wrap: anywhere !important;
                word-break: break-word !important;
                text-align: left !important;
                justify-content: flex-start !important;
                align-items: flex-start !important;
                padding: 0.7rem 1.8rem 0.7rem 0.8rem !important;
                line-height: 1.35 !important;
                position: relative;
                font-weight: 500 !important;
            }
            @media (max-width: 900px) {
                [class*="st-key-rsa_landing_panel"] {
                    padding: 1.15rem 0.9rem 0.85rem !important;
                }
                [class*="st-key-rsa_landing_hero_actions"]
                    div[data-testid="stHorizontalBlock"] {
                    flex-direction: column !important;
                }
                [class*="st-key-rsa_landing_hero_actions"]
                    div[data-testid="stColumn"] {
                    width: 100% !important;
                    flex: 1 1 auto !important;
                }
                .rsa-landing-how { grid-template-columns: 1fr; }
            }
            </style>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_zero_results() -> None:
    """Post-search empty state. Must not look like the first-use welcome page."""
    st.markdown(
        '<div class="rsa-landing-zero">'
        "<p><strong>No matching properties found.</strong></p>"
        "<p>Adjust Search Preferences or try another search in chat.</p>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_landing_page(
    prefs: Mapping[str, Any] | None,
    *,
    on_search: Callable[[], None],
    on_ask_in_chat: Callable[[], None],
) -> None:
    """Central pre-search welcome panel."""
    ready = saved_preferences_ready_for_search(prefs)
    chips = landing_preference_chips(prefs)
    saved_prefs_heading = landing_saved_prefs_heading(prefs)

    with st.container(key="rsa_landing_panel"):
        st.markdown(
            '<div class="rsa-landing-hero">'
            '<div class="rsa-landing-eyebrow">Welcome to</div>'
            '<div class="rsa-landing-title">Find your next property</div>'
            f'<p class="rsa-landing-lead">{html.escape(LANDING_HERO_LEAD)}</p>'
            "</div>",
            unsafe_allow_html=True,
        )

        with st.container(key="rsa_landing_hero_actions"):
            search_col, ask_col = st.columns(2)
            with search_col:
                if st.button(
                    "Search with preferences",
                    key="rsa_landing_search",
                    type="primary",
                    icon=":material/search:",
                    use_container_width=True,
                    disabled=not ready,
                ):
                    on_search()
            with ask_col:
                if st.button(
                    "Ask in chat",
                    key="rsa_landing_ask",
                    icon=":material/chat_bubble:",
                    use_container_width=True,
                ):
                    on_ask_in_chat()
        st.markdown(
            f'<div class="rsa-landing-section-title">{html.escape(saved_prefs_heading)}</div>',
            unsafe_allow_html=True,
        )
        if chips:
            chip_html = "".join(
                _preference_chip_html(kind, label) for kind, label in chips
            )
            st.markdown(
                f'<div class="rsa-landing-chip-row">{chip_html}</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div class="rsa-landing-how-heading">{html.escape(HOW_IT_WORKS_HEADING)}</div>'
            f'<p class="rsa-landing-how-sub">{html.escape(HOW_IT_WORKS_SUBTITLE)}</p>',
            unsafe_allow_html=True,
        )
        st.markdown(_how_it_works_html(), unsafe_allow_html=True)
        st.markdown(
            '<div class="rsa-landing-footer">Smarter property search, powered by AI.</div>',
            unsafe_allow_html=True,
        )


def render_chat_empty_state(*, on_prompt: Callable[[str], None]) -> None:
    """Chat onboarding: intro + three complete starter prompts."""
    st.markdown(
        '<div class="rsa-chat-empty">'
        f'<div class="rsa-chat-empty-icon">{_SVG_ICONS["chat"]}</div>'
        '<div class="rsa-chat-empty-title">Search naturally with chat</div>'
        '<p class="rsa-chat-empty-lead">Ask questions, refine your search, or get '
        "insights about properties.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    with st.container(key="rsa_chat_starters"):
        for i, prompt in enumerate(CHAT_STARTER_PROMPTS):
            if st.button(
                prompt,
                key=f"rsa_chat_starter_{i}",
                use_container_width=True,
            ):
                on_prompt(prompt)


def _preference_chip_html(kind: str, label: str) -> str:
    icon = _SVG_ICONS.get(_CHIP_ICON_ALIASES.get(kind, kind), "")
    return (
        f'<span class="rsa-landing-chip">{icon}'
        f"<span>{html.escape(label)}</span></span>"
    )


def _feature_chip_html(label: str) -> str:
    return f'<span class="rsa-landing-feature-chip">{html.escape(label)}</span>'


def _how_it_works_html() -> str:
    parts = ['<div class="rsa-landing-how">']
    for step in HOW_IT_WORKS_STEPS:
        chips = "".join(_feature_chip_html(label) for label in step.chips)
        parts.append(
            '<div class="rsa-landing-how-card">'
            f'<div class="rsa-landing-how-top">'
            f'<span class="rsa-landing-how-num">{html.escape(step.number)}</span>'
            f"{_SVG_ICONS[step.icon]}"
            "</div>"
            f'<div class="rsa-landing-how-title">{html.escape(step.title)}</div>'
            f'<p class="rsa-landing-how-copy">{html.escape(step.copy)}</p>'
            f'<div class="rsa-landing-feature-chips">{chips}</div>'
            "</div>"
        )
    parts.append("</div>")
    return "".join(parts)
