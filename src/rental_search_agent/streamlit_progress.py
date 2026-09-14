"""Streamlit renderer for the central search-progress panel."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from rental_search_agent.search_progress import (
    PROGRESS_CSS,
    SearchProgressState,
    invoke_progress,
    render_progress_html,
)

_BOUND_PANEL: SearchProgressPanel | None = None


def inject_search_progress_css() -> None:
    """Panel styles in the main document so type inherits the app font."""
    with st.container(key="rsa_hidden_css_progress"):
        st.markdown(
            f"""
            <style>
            [class*="st-key-rsa_search_progress"] {{
                max-width: 52rem;
                margin: 0.2rem auto 0.6rem !important;
            }}
            [class*="st-key-rsa_search_progress"] [data-testid="stMarkdownContainer"] p {{
                margin: 0;
            }}
            {PROGRESS_CSS}
            </style>
            """,
            unsafe_allow_html=True,
        )


def search_workflow_pending(session_state: Mapping[str, Any] | None) -> bool:
    """True when a sidebar/chat search will run during this script pass."""
    state = session_state or {}
    return bool(
        state.get("_pending_pref_search")
        or state.get("pending_chat_prompt")
        or state.get("_pending_agent_step")
    )


class SearchProgressPanel:
    """One stable placeholder that is rewritten in place as events arrive."""

    def __init__(self, placeholder: Any) -> None:
        self._placeholder = placeholder
        self.state = SearchProgressState()
        self._cleared = False

    def handle(
        self,
        name: str,
        phase: str,
        ok: bool = True,
        listing_count: int | None = None,
    ) -> None:
        self.state.handle(name, phase, ok=ok, listing_count=listing_count)
        if self.state.started and not self._cleared:
            self._render()

    def finish(self) -> None:
        if self.state.started and not self.state.is_complete:
            self.state.complete()
        self._placeholder.empty()
        self._cleared = True

    def _render(self) -> None:
        markup = render_progress_html(self.state.view())
        self._placeholder.markdown(markup, unsafe_allow_html=True)


def bind_search_progress_panel(panel: SearchProgressPanel | None) -> None:
    global _BOUND_PANEL
    _BOUND_PANEL = panel


def get_bound_search_progress_panel() -> SearchProgressPanel | None:
    return _BOUND_PANEL


def bound_progress_callback() -> Any:
    panel = _BOUND_PANEL
    if panel is None:
        return None
    return panel.handle


def emit_to_bound_panel(
    name: str,
    phase: str,
    ok: bool = True,
    listing_count: int | None = None,
) -> None:
    invoke_progress(bound_progress_callback(), name, phase, ok=ok, listing_count=listing_count)
