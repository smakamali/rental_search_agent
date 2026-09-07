"""Streamlit listing-analysis UI: gauges, metadata, criteria, highlights."""

from __future__ import annotations

import html
import math
from typing import Optional

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
)
from rental_search_agent.display_format import escape_markdown_link_text, get_score_color, safe_http_url, score_to_pct

_GAUGE_SIZES = {"large": 176, "small": 108}


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
        .rsa-crit-row {
            display: grid;
            grid-template-columns: 1.4rem minmax(6rem, 1.2fr) minmax(8rem, 1.6fr) auto;
            gap: 0.35rem 0.6rem; align-items: baseline;
            padding: 0.35rem 0; border-bottom: 1px solid var(--rsa-muted, rgba(128,128,128,0.25));
            font-size: 0.92rem;
        }
        .rsa-crit-status { font-weight: 700; }
        .rsa-crit-name { font-weight: 600; }
        .rsa-crit-values { opacity: 0.92; }
        .rsa-badge {
            font-size: 0.7rem; font-weight: 600; letter-spacing: 0.03em;
            text-transform: uppercase; opacity: 0.7; white-space: nowrap;
        }
        .rsa-crit-unknown .rsa-crit-status,
        .rsa-crit-unknown .rsa-crit-values { opacity: 0.72; }
        .rsa-crit-unmet .rsa-crit-status { color: #e67e22; }
        .rsa-highlight {
            padding: 0.55rem 0.15rem 0.35rem;
            border-bottom: 1px solid var(--rsa-muted, rgba(128,128,128,0.25));
        }
        .rsa-highlight-title { font-weight: 650; margin-bottom: 0.15rem; }
        .rsa-highlight-body { font-size: 0.9rem; opacity: 0.88; line-height: 1.35; }
        .rsa-open-item { padding: 0.3rem 0; font-size: 0.92rem; opacity: 0.9; }
        @media (max-width: 700px) {
            .rsa-crit-row {
                grid-template-columns: 1.4rem 1fr;
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
    outer_label = "" if large else f'<div class="rsa-gauge-caption">{html.escape(label)}</div>'
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


def render_listing_header(view: AnalysisView, listing: dict) -> None:
    listing_url = safe_http_url(listing.get("url"))
    col_title, col_actions = st.columns([3.2, 1])
    with col_title:
        st.markdown(f"<h3>{html.escape(view.headline)}</h3>", unsafe_allow_html=True)
        if view.locality:
            st.caption(view.locality)
    with col_actions:
        if listing_url:
            st.link_button("View listing", listing_url, use_container_width=True)
        video = safe_http_url(listing.get("video_url"))
        if video:
            st.link_button("Video / tour", video, use_container_width=True)


def render_listing_metadata(listing: dict) -> None:
    fields: list[tuple[str, str]] = []
    mls = listing.get("id")
    listing_url = safe_http_url(listing.get("url"))
    if mls:
        fields.append(("MLS", str(mls)))
    ptype = listing_property_type(listing)
    if ptype:
        fields.append(("Property type", ptype))
    pcat = listing_property_category(listing)
    if pcat:
        fields.append(("Category", pcat))
    listed = listing.get("listing_age_display")
    if listed:
        fields.append(("Listed", str(listed)))
    agent = listing.get("agent_name")
    if agent:
        fields.append(("Listing agent", str(agent)))
    phone = listing.get("agent_phone")
    if phone:
        fields.append(("Agent phone", str(phone)))
    brokerage = listing.get("brokerage_name")
    if brokerage:
        fields.append(("Brokerage", str(brokerage)))
    if listing.get("lot_size"):
        fields.append(("Lot size", str(listing["lot_size"])))
    if listing.get("open_house"):
        fields.append(("Open house", str(listing["open_house"])))
    if listing.get("price_change_display"):
        fields.append(("Price change", str(listing["price_change_display"])))

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
            for col, (label, value) in zip(cols, chunk):
                with col:
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
            help_text="Overall match is a weighted average of the component scores that could be computed. Missing components are omitted, not treated as zero.",
        )
        st.markdown(f"**{html.escape(view.strength_label)}**")
        st.caption(view.strength_blurb)
        if view.total_count:
            st.caption(
                f"**{view.evaluated_count} of {view.total_count}** search criteria evaluated"
                + (
                    f"  ·  **{view.unknown_count}** listing details not available"
                    if view.unknown_count
                    else ""
                )
            )
        st.caption(
            "Criteria evaluated reflects whether each search criterion could be processed. "
            "It is not part of the overall match score."
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
        if view.show_semantic_note:
            st.info(
                "Semantic similarity can be lower when the listing provides limited "
                "information about lifestyle or qualitative preferences. Explicit "
                "requirements (price, beds, commute, listed amenities) are scored separately."
            )


def _criteria_row_html(row: CriteriaRow) -> str:
    status_cls = {
        "met": "rsa-crit-met",
        "partial": "rsa-crit-partial",
        "unmet": "rsa-crit-unmet",
        "unknown": "rsa-crit-unknown",
    }.get(row.status, "rsa-crit-unknown")
    aria = f"{row.status_label}: {row.name}. {row.comparison_text}"
    badge = html.escape(row.source) if row.source else "—"
    return (
        f'<div class="rsa-crit-row {status_cls}" role="listitem" aria-label="{html.escape(aria)}">'
        f'<span class="rsa-crit-status" aria-hidden="true">{html.escape(row.marker)}</span>'
        f'<span class="rsa-crit-name">{html.escape(row.name)}</span>'
        f'<span class="rsa-crit-values">{html.escape(row.comparison_text)}</span>'
        f'<span class="rsa-badge">{badge}</span>'
        f"</div>"
    )


def render_criteria_group(title: str, rows: list[CriteriaRow]) -> None:
    if not rows:
        return
    st.markdown(f"**{html.escape(title)}**")
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
        parts.append(
            '<div class="rsa-highlight">'
            f'<div class="rsa-highlight-title">{html.escape(h.title)}</div>'
            f'<div class="rsa-highlight-body">{html.escape(h.body)}</div>'
            "</div>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_open_questions(view: AnalysisView) -> None:
    st.subheader("Open questions")
    st.caption(
        "These details were not found in the listing. They do not necessarily reduce "
        "the match score, but may be worth confirming."
    )
    if not view.open_questions:
        st.caption("No missing listing details for your search criteria.")
        return
    parts = []
    for row in view.open_questions:
        parts.append(
            '<div class="rsa-open-item">'
            f"? {html.escape(row.name)}"
            f'<div class="rsa-highlight-body">{html.escape(row.comparison_text)}</div>'
            "</div>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)
    if view.unmet:
        st.markdown("**Unmet criteria**")
        for row in view.unmet:
            st.write(f"✕ {row.name} — {row.comparison_text}")


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
            st.caption("Weights used for this listing (after dropping missing components): " + ", ".join(used) + ".")
        st.markdown("**What each component measures**")
        for key in COMPONENT_ORDER:
            st.markdown(f"- **{key.capitalize()}:** {COMPONENT_HELP[key]}")
        st.caption(
            "Criteria evaluated (formerly shown as coverage) is a separate checklist "
            "summary and is not included in the overall match."
        )


def render_listing_analysis(listing: dict, result: dict) -> None:
    """Full Analyze panel for one listing."""
    inject_analysis_css()
    view = build_analysis_view(listing, result)
    with st.container():
        render_listing_header(view, listing)
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
            render_score_explanation(view)
