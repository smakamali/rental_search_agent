"""Streamlit listing-analysis UI: gauges, metadata, criteria, highlights."""

from __future__ import annotations

import html
import math
from typing import Callable, Optional

import streamlit as st

from rental_search_agent.analysis_view import (
    COMPONENT_CAPTION,
    COMPONENT_HELP,
    COMPONENT_ORDER,
    GROUP_ORDER,
    GROUP_TITLES,
    AnalysisView,
    CriteriaRow,
    build_analysis_view,
    format_weight_pct,
    listing_property_category,
    listing_property_type,
    weighted_score_line,
)
from rental_search_agent.display_format import (
    escape_markdown_link_text,
    format_currency,
    get_score_color,
    safe_http_url,
    score_to_pct,
)
from rental_search_agent.ui_assets import highlight_icon_img_html, status_icon_img_html

_GAUGE_SIZES = {"large": 176, "small": 108}

_MLS_CATEGORY_HELP = (
    "Broad category supplied by the listing source. Property type is the more "
    "specific building format."
)
_CRITERIA_EVAL_HELP = (
    "Criteria evaluated indicates whether each search criterion had enough evidence "
    "to be assessed. It is separate from the overall Match score."
)
_WEIGHTED_SCORE_HELP = (
    "The overall Match is a weighted average of available component scores. Missing "
    "components are omitted and remaining weights are renormalized."
)


def inject_analysis_css() -> None:
    """Scoped styles for the listing-analysis panel (theme-variable friendly)."""
    st.markdown(
        """
        <style>
        .rsa-analysis { --rsa-muted: rgba(128,128,128,0.35); }
        .rsa-gauge-wrap {
            display: flex; flex-direction: column; align-items: center;
            text-align: center; gap: 0.15rem;
        }
        .rsa-gauge-wrap svg { display: block; }
        .rsa-gauge-pct {
            font-size: 22px; font-weight: 700; fill: currentColor;
            font-family: inherit;
        }
        .rsa-gauge-wrap.rsa-gauge-large .rsa-gauge-pct { font-size: 28px; }
        .rsa-gauge-sub {
            font-size: 11px; font-weight: 600; fill: currentColor; opacity: 0.75;
            font-family: inherit; text-transform: uppercase; letter-spacing: 0.04em;
        }
        .rsa-gauge-caption {
            font-size: 0.8rem; opacity: 0.78; max-width: 11rem; line-height: 1.3;
        }
        .rsa-gauge-row {
            display: flex; flex-wrap: wrap; justify-content: space-around;
            gap: 0.5rem 0.35rem; width: 100%;
        }
        .rsa-gauge-row .rsa-gauge-wrap { flex: 1 1 5.5rem; min-width: 5.5rem; }
        .rsa-weight-line {
            font-size: 0.82rem; opacity: 0.82; margin: 0.55rem 0 0.25rem;
            line-height: 1.35;
        }
        .rsa-eval-line {
            font-size: 0.85rem; opacity: 0.85; margin-top: 0.35rem;
        }
        .rsa-crit-row {
            display: grid;
            grid-template-columns: 1.5rem minmax(6rem, 1.2fr) minmax(8rem, 1.6fr) auto;
            gap: 0.35rem 0.6rem; align-items: center;
            padding: 0.28rem 0; border-bottom: 1px solid var(--rsa-muted, rgba(128,128,128,0.25));
            font-size: 0.9rem;
        }
        .rsa-crit-status {
            display: inline-flex; align-items: center; justify-content: center;
            line-height: 0;
        }
        .rsa-status-icon {
            width: 20px; height: 20px; object-fit: contain;
            display: block; vertical-align: middle;
        }
        .rsa-status-fallback { font-weight: 700; font-size: 0.95rem; line-height: 1; }
        .rsa-crit-unknown .rsa-crit-values { opacity: 0.72; }
        .rsa-crit-name { font-weight: 600; }
        .rsa-crit-values { opacity: 0.92; }
        .rsa-badge {
            display: inline-flex; align-items: center; gap: 0.2rem;
            font-size: 0.68rem; font-weight: 650; letter-spacing: 0.03em;
            text-transform: uppercase; opacity: 0.78; white-space: nowrap;
            border: 1px solid rgba(128,128,128,0.35); border-radius: 4px;
            padding: 0.1rem 0.35rem;
        }
        .rsa-info {
            display: inline-flex; align-items: center; justify-content: center;
            width: 0.95rem; height: 0.95rem; border-radius: 50%;
            border: 1px solid currentColor; opacity: 0.65; font-size: 0.65rem;
            font-weight: 700; cursor: help; vertical-align: middle;
            margin-left: 0.2rem; text-decoration: none; color: inherit;
        }
        .rsa-info:focus { outline: 2px solid currentColor; outline-offset: 1px; opacity: 1; }
        .rsa-highlight {
            display: grid; grid-template-columns: 2.6rem 1fr; gap: 0.55rem;
            align-items: start; padding: 0.5rem 0.15rem 0.35rem;
            border-bottom: 1px solid var(--rsa-muted, rgba(128,128,128,0.25));
        }
        .rsa-highlight-icon {
            width: 2.5rem; height: 2.5rem;
            display: flex; align-items: center; justify-content: center;
            line-height: 0;
            /* Artwork already includes circular colored background — no extra circle. */
            background: transparent;
        }
        .rsa-highlight-icon-img {
            width: 40px; height: 40px; object-fit: contain;
            display: block;
        }
        .rsa-highlight-title { font-weight: 650; margin-bottom: 0.1rem; }
        .rsa-highlight-body { font-size: 0.88rem; opacity: 0.88; line-height: 1.35; }
        .rsa-open-item {
            display: grid; grid-template-columns: 1.5rem 1fr; gap: 0.4rem;
            align-items: start; padding: 0.3rem 0; font-size: 0.9rem;
        }
        .rsa-open-mark {
            display: inline-flex; align-items: center; justify-content: center;
            line-height: 0; padding-top: 0.1rem;
        }
        .rsa-open-empty {
            display: flex; gap: 0.55rem; align-items: flex-start;
            padding: 0.55rem 0.65rem; border-radius: 8px;
            border: 1px solid rgba(39, 174, 96, 0.35);
            background: rgba(39, 174, 96, 0.1);
        }
        .rsa-open-empty-mark {
            display: inline-flex; align-items: center; justify-content: center;
            line-height: 0; flex-shrink: 0; padding-top: 0.1rem;
        }
        .rsa-open-empty-title { font-weight: 650; }
        .rsa-open-empty-body { font-size: 0.85rem; opacity: 0.88; }
        .rsa-group-title {
            font-size: 0.78rem; font-weight: 650; letter-spacing: 0.04em;
            text-transform: uppercase; opacity: 0.7; margin: 0.55rem 0 0.15rem;
        }
        @media (max-width: 700px) {
            .rsa-crit-row {
                grid-template-columns: 1.5rem 1fr;
                grid-template-areas:
                    "status name"
                    ". values"
                    ". badge";
            }
            .rsa-crit-status { grid-area: status; }
            .rsa-crit-name { grid-area: name; }
            .rsa-crit-values { grid-area: values; }
            .rsa-badge { grid-area: badge; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _info_icon_html(help_text: str, *, aria_label: str = "More information") -> str:
    esc = html.escape(help_text)
    return (
        f'<a class="rsa-info" href="#" role="img" tabindex="0" '
        f'title="{esc}" aria-label="{html.escape(aria_label)}">i</a>'
    )


def _gauge_svg_html(
    score_pct: Optional[int],
    label: str,
    *,
    size: int,
    large: bool = False,
    caption: str | None = None,
    help_text: str | None = None,
) -> str:
    r = 42.0
    c = 2.0 * math.pi * r
    pct = 0 if score_pct is None else max(0, min(100, int(score_pct)))
    color = get_score_color(pct) if score_pct is not None else "#888888"
    dash = c * (pct / 100.0)
    gap = c - dash
    pct_text = "—" if score_pct is None else f"{pct}%"
    aria = (
        f"{html.escape(label)} match score: unavailable"
        if score_pct is None
        else f"{html.escape(label)} match score: {pct} percent"
    )
    size_cls = "rsa-gauge-large" if large else "rsa-gauge-small"
    inner_label = "Match" if large else ""
    sub = (
        f'<text x="60" y="76" text-anchor="middle" class="rsa-gauge-sub">{html.escape(inner_label)}</text>'
        if inner_label
        else ""
    )
    pct_y = "58" if inner_label else "64"
    caption_html = (
        f'<div class="rsa-gauge-caption">{html.escape(caption)}</div>' if caption else ""
    )
    if large:
        outer_label = ""
    else:
        help_html = _info_icon_html(help_text, aria_label=f"About {label}") if help_text else ""
        outer_label = (
            f'<div class="rsa-gauge-caption">{html.escape(label)}{help_html}</div>'
        )
    title_attr = f' title="{html.escape(help_text)}"' if help_text else ""
    return (
        f'<div class="rsa-gauge-wrap {size_cls}" role="img" aria-label="{aria}"{title_attr}>'
        f'<svg width="{size}" height="{size}" viewBox="0 0 120 120" aria-hidden="true">'
        f'<circle cx="60" cy="60" r="{r:.1f}" fill="none" stroke="currentColor" '
        f'stroke-opacity="0.18" stroke-width="10"/>'
        f'<circle cx="60" cy="60" r="{r:.1f}" fill="none" stroke="{html.escape(color)}" '
        f'stroke-width="10" stroke-linecap="round" '
        f'stroke-dasharray="{dash:.2f} {gap:.2f}" transform="rotate(-90 60 60)"/>'
        f'<text x="60" y="{pct_y}" text-anchor="middle" class="rsa-gauge-pct">'
        f"{html.escape(pct_text)}</text>{sub}</svg>"
        f"{outer_label}{caption_html}</div>"
    )


def render_score_gauge(
    score: float | int | None,
    label: str,
    size: str = "small",
    caption: str | None = None,
    help_text: str | None = None,
) -> None:
    """Reusable circular score gauge (inline SVG). score is 0–100 or 0–1."""
    pct = score_to_pct(score) if score is not None else None
    px = _GAUGE_SIZES.get(size, _GAUGE_SIZES["small"])
    st.markdown(
        _gauge_svg_html(
            pct, label, size=px, large=(size == "large"), caption=caption, help_text=help_text
        ),
        unsafe_allow_html=True,
    )


def _render_photo(listing: dict, width: int = 280) -> None:
    photo_url = listing.get("photo_url") or ""
    listing_url = safe_http_url(listing.get("url")) or ""
    if photo_url and listing_url:
        st.markdown(
            f'<a href="{html.escape(listing_url)}" target="_blank" rel="noopener">'
            f'<img src="{html.escape(str(photo_url))}" width="{width}" '
            f'style="border-radius:8px;max-width:100%;height:auto;" alt="Listing photo"></a>',
            unsafe_allow_html=True,
        )
    elif photo_url:
        st.image(photo_url, width=width)
    elif listing_url:
        st.link_button("View listing", listing_url)
    else:
        st.caption("No photo available")


def render_listing_header(
    view: AnalysisView,
    listing: dict,
    *,
    on_close: Callable[[], None] | None = None,
) -> None:
    listing_url = safe_http_url(listing.get("url"))
    col_title, col_actions = st.columns([3.2, 1.2])
    with col_title:
        st.markdown(f"<h3>{html.escape(view.headline)}</h3>", unsafe_allow_html=True)
        if view.locality:
            st.caption(view.locality)
    with col_actions:
        if on_close is not None:
            if st.button("Close analysis", key="rsa_close_analysis", use_container_width=True):
                on_close()
                st.rerun()
        if listing_url:
            st.link_button("View listing", listing_url, use_container_width=True, type="primary")
        video = safe_http_url(listing.get("video_url"))
        if video:
            st.link_button("Video / tour", video, use_container_width=True)


def render_listing_metadata(listing: dict) -> None:
    fields: list[tuple[str, str, str | None]] = []
    price = listing.get("price")
    if price is not None and price != "":
        fields.append(("Price", format_currency(price), None))
    mls = listing.get("id")
    listing_url = safe_http_url(listing.get("url"))
    if mls:
        fields.append(("MLS", str(mls), None))
    ptype = listing_property_type(listing)
    if ptype:
        fields.append(("Property type", ptype, None))
    pcat = listing_property_category(listing)
    if pcat:
        fields.append(("MLS category", pcat, _MLS_CATEGORY_HELP))
    listed = listing.get("listing_age_display")
    if listed:
        fields.append(("Listed", str(listed), None))
    agent = listing.get("agent_name")
    if agent:
        fields.append(("Listing agent", str(agent), None))
    phone = listing.get("agent_phone")
    if phone:
        fields.append(("Agent phone", str(phone), None))
    brokerage = listing.get("brokerage_name")
    if brokerage:
        fields.append(("Brokerage", str(brokerage), None))
    if listing.get("lot_size"):
        fields.append(("Lot size", str(listing["lot_size"]), None))
    if listing.get("open_house"):
        fields.append(("Open house", str(listing["open_house"]), None))
    if listing.get("price_change_display"):
        fields.append(("Price change", str(listing["price_change_display"]), None))

    photo_col, meta_col = st.columns([1.1, 1.9])
    with photo_col:
        _render_photo(listing, width=320)
    with meta_col:
        if not fields:
            st.caption("No listing details available.")
            return
        n = 2
        for i in range(0, len(fields), n):
            chunk = fields[i : i + n]
            cols = st.columns(n)
            for col, (label, value, help_text) in zip(cols, chunk):
                with col:
                    if help_text:
                        st.caption(label, help=help_text)
                    else:
                        st.caption(label)
                    if label == "MLS" and listing_url:
                        st.markdown(
                            f"[{escape_markdown_link_text(value)}]({listing_url})"
                        )
                    else:
                        st.write(value)


def render_match_summary(view: AnalysisView) -> None:
    st.subheader("Overall match")
    left, right = st.columns([1.05, 1.55])
    with left:
        render_score_gauge(
            view.match_pct,
            "Match",
            size="large",
            help_text=(
                "Overall match is a weighted average of the component scores that could "
                "be computed. Missing components are omitted, not treated as zero."
            ),
        )
        st.markdown(f"**{html.escape(view.strength_label)}**")
        st.caption(view.strength_blurb)
        if view.total_count:
            eval_bits = f"{view.evaluated_count} / {view.total_count} criteria evaluated"
            if view.unknown_count:
                eval_bits += f" · {view.unknown_count} unavailable"
            st.markdown(
                f'<div class="rsa-eval-line">{html.escape(eval_bits)}'
                f"{_info_icon_html(_CRITERIA_EVAL_HELP, aria_label='About criteria evaluated')}"
                f"</div>",
                unsafe_allow_html=True,
            )
    with right:
        st.markdown("**Score breakdown**")
        gauges = []
        for key in COMPONENT_ORDER:
            val = view.components.get(key)
            if key not in view.included and val is None:
                continue
            pct = score_to_pct(val)
            gauges.append(
                _gauge_svg_html(
                    pct,
                    key.capitalize(),
                    size=_GAUGE_SIZES["small"],
                    caption=COMPONENT_CAPTION.get(key),
                    help_text=COMPONENT_HELP.get(key),
                )
            )
        if gauges:
            st.markdown(
                '<div class="rsa-gauge-row">' + "".join(gauges) + "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No component scores available.")
        weight_line = weighted_score_line(view.weights_used)
        if weight_line:
            st.markdown(
                f'<div class="rsa-weight-line">{html.escape(weight_line)}'
                f"{_info_icon_html(_WEIGHTED_SCORE_HELP, aria_label='About weighted score')}"
                f"</div>",
                unsafe_allow_html=True,
            )
        render_score_explanation(view)


def _criteria_row_html(row: CriteriaRow) -> str:
    status_cls = {
        "met": "rsa-crit-met",
        "partial": "rsa-crit-partial",
        "unmet": "rsa-crit-unmet",
        "unknown": "rsa-crit-unknown",
    }.get(row.status, "rsa-crit-unknown")
    aria = f"{row.status_label}: {row.name}. {row.comparison_text}"
    badge_label = html.escape(row.source_label)
    help_html = ""
    if row.source_help:
        help_html = _info_icon_html(row.source_help, aria_label=f"About {row.source_label}")
    icon_html = status_icon_img_html(row.status, size_px=20)
    return (
        f'<div class="rsa-crit-row {status_cls}" role="listitem" aria-label="{html.escape(aria)}">'
        f'<span class="rsa-crit-status">{icon_html}</span>'
        f'<span class="rsa-crit-name">{html.escape(row.name)}</span>'
        f'<span class="rsa-crit-values">{html.escape(row.comparison_text)}</span>'
        f'<span class="rsa-badge">{badge_label}{help_html}</span>'
        f"</div>"
    )


def render_criteria_group(title: str, rows: list[CriteriaRow]) -> None:
    if not rows:
        return
    st.markdown(
        f'<div class="rsa-group-title">{html.escape(title)}</div>',
        unsafe_allow_html=True,
    )
    body = "".join(_criteria_row_html(r) for r in rows)
    st.markdown(f'<div role="list">{body}</div>', unsafe_allow_html=True)


def render_search_criteria(view: AnalysisView) -> None:
    st.subheader("Your search criteria")
    any_rows = False
    for group in GROUP_ORDER:
        rows = view.criteria_by_group.get(group) or []
        if not rows:
            continue
        any_rows = True
        render_criteria_group(GROUP_TITLES.get(group, group.title()), rows)
    if not any_rows:
        st.caption("No structured search criteria were available for this listing.")


def render_property_highlights(view: AnalysisView) -> None:
    st.subheader("Why this property stands out")
    if not view.highlights:
        st.caption("No standout matches to highlight from the structured criteria.")
        return
    parts = []
    for h in view.highlights:
        icon_html = highlight_icon_img_html(h.icon_key, size_px=40)
        parts.append(
            '<div class="rsa-highlight">'
            f'<div class="rsa-highlight-icon">{icon_html}</div>'
            "<div>"
            f'<div class="rsa-highlight-title">{html.escape(h.title)}</div>'
            f'<div class="rsa-highlight-body">{html.escape(h.body)}</div>'
            "</div></div>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_open_questions(view: AnalysisView) -> None:
    st.subheader("Open questions")
    if not view.open_questions:
        st.markdown(
            '<div class="rsa-open-empty" role="status" '
            'aria-label="No open questions. All current criteria had enough listing evidence.">'
            f'<div class="rsa-open-empty-mark">{status_icon_img_html("met", size_px=20)}</div>'
            "<div>"
            '<div class="rsa-open-empty-title">No open questions</div>'
            '<div class="rsa-open-empty-body">'
            "All current criteria had enough listing evidence."
            "</div></div></div>",
            unsafe_allow_html=True,
        )
        return
    parts = []
    for row in view.open_questions:
        aria = f"Open question: {row.name}. {row.comparison_text}"
        parts.append(
            f'<div class="rsa-open-item" role="listitem" aria-label="{html.escape(aria)}">'
            f'<span class="rsa-open-mark">{status_icon_img_html("unknown", size_px=20)}</span>'
            "<div>"
            f"<strong>{html.escape(row.name)}</strong>"
            f'<div class="rsa-highlight-body">{html.escape(row.comparison_text)}</div>'
            "</div></div>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_score_explanation(view: AnalysisView) -> None:
    with st.expander("About the scores", expanded=False):
        st.markdown(
            "The **overall match** is a weighted average of the component scores that "
            "could be computed for this listing. Missing components are left out — they "
            "are not treated as zero — and the remaining weights are renormalized."
        )
        configured = view.configured_weights
        if configured:
            bits = [
                f"{k.capitalize()} ({format_weight_pct(w)})"
                for k, w in configured.items()
                if k in COMPONENT_ORDER
            ]
            st.markdown("Configured weights: " + ", ".join(bits) + ".")
        if view.weights_used:
            used = [
                f"{k.capitalize()} ({format_weight_pct(w)})"
                for k, w in view.weights_used.items()
            ]
            st.caption(
                "Weights used for this listing (after dropping missing components): "
                + ", ".join(used)
                + "."
            )
        st.markdown("**What each component measures**")
        for key in COMPONENT_ORDER:
            st.markdown(f"- **{key.capitalize()}:** {COMPONENT_HELP[key]}")
        st.caption(
            "Criteria evaluated is a separate checklist summary and is not included "
            "in the overall match."
        )


def render_listing_analysis(
    listing: dict,
    result: dict,
    *,
    on_close: Callable[[], None] | None = None,
) -> None:
    """Full Analyze panel for one listing."""
    inject_analysis_css()
    view = build_analysis_view(listing, result)
    with st.container():
        render_listing_header(view, listing, on_close=on_close)
        render_listing_metadata(listing)
        st.divider()
        render_match_summary(view)
        st.divider()
        left, right = st.columns([1.15, 1])
        with left:
            render_search_criteria(view)
        with right:
            render_property_highlights(view)
            render_open_questions(view)
