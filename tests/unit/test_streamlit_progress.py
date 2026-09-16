"""Tests for the Streamlit search-progress panel wiring."""

import inspect

from rental_search_agent.search_progress import STAGE_PREPARE
from rental_search_agent.streamlit_app import (
    _execute_preference_search,
    _run_agent_step_with_ui,
    _run_sidebar_search,
    render_app_header,
)
from rental_search_agent.streamlit_landing import center_panel_kind
from rental_search_agent.streamlit_progress import (
    SearchProgressPanel,
    search_workflow_pending,
)


class TestCenterPanelProgress:
    def test_in_progress_wins_over_results(self):
        assert (
            center_panel_kind(
                listings=[{"id": "a"}],
                display_source="score",
                search_master=[{"id": "a"}],
                in_progress=True,
            )
            == "progress"
        )

    def test_pending_flags(self):
        assert search_workflow_pending({}) is False
        assert search_workflow_pending({"_pending_pref_search": {"spinner": "x"}}) is True
        assert search_workflow_pending({"pending_chat_prompt": "hi"}) is True
        assert search_workflow_pending({"_pending_agent_step": True}) is True


class TestSharedComponent:
    def test_sidebar_and_chat_use_same_central_component(self):
        sidebar_src = inspect.getsource(_run_sidebar_search)
        chat_src = inspect.getsource(_run_agent_step_with_ui)
        execute_src = inspect.getsource(_execute_preference_search)
        assert "progress" in sidebar_src
        assert STAGE_PREPARE in sidebar_src or "STAGE_PREPARE" in sidebar_src
        assert "bound_progress_callback" in chat_src
        assert "run_agent_step_events" in chat_src
        assert "_pending_pref_search" in execute_src
        assert "SearchProgressPanel" in inspect.getsource(
            __import__("rental_search_agent.streamlit_progress", fromlist=["SearchProgressPanel"])
        )

    def test_execute_preference_search_queues_instead_of_running(self):
        src = inspect.getsource(_execute_preference_search)
        assert "_pending_pref_search" in src
        assert "_run_sidebar_search" not in src

    def test_panel_renders_in_page_markdown_not_iframe(self):
        from rental_search_agent import streamlit_progress as progress_mod
        from rental_search_agent.streamlit_app import (
            _inject_app_chrome_css,
            _inject_chat_blob_css,
            _inject_offscreen_chrome,
            _main_body,
        )

        render_src = inspect.getsource(progress_mod.SearchProgressPanel._render)
        chrome_src = inspect.getsource(_inject_app_chrome_css)
        offscreen_src = inspect.getsource(_inject_offscreen_chrome)
        main_src = inspect.getsource(_main_body)
        header_src = inspect.getsource(render_app_header)
        assert "markdown" in render_src
        assert "components.html" not in render_src
        assert "PROGRESS_CSS" in chrome_src
        assert "inject_search_progress_css" not in main_src
        assert "_inject_app_chrome_css" in offscreen_src
        assert "st.sidebar" in offscreen_src
        assert "_inject_offscreen_chrome" in main_src
        assert "_inject_app_chrome_css" not in header_src
        assert "_inject_sidebar_restore_control" not in header_src
        assert "rsa_search_progress_slot" in main_src
        assert "st.empty()" in main_src
        assert main_src.index("rsa_search_progress_slot") < main_src.index(
            "if pending_pref or chat_work_pending"
        )
        assert "st.rerun()" in main_src
        assert "style:only-child" not in chrome_src
        assert (
            'stVerticalBlockBorderWrapper"]:has(> [class*="st-key-rsa_hidden_"]'
            not in chrome_src
        )
        assert "position: fixed" in chrome_src
        assert "rsa-progress-overlay" in chrome_src
        assert "background: #0e1116" in chrome_src
        assert "min(21rem, 28vw)" in chrome_src
        assert "--sidebar-width" not in chrome_src
        chat_src = inspect.getsource(_inject_chat_blob_css)
        assert "stChatMessage" in chat_src
        assert "flex: 0 0 auto" in chat_src
        assert "st-key-chat_blob" in chat_src
        assert "height: 0" in chat_src


class TestPanelHandle:
    def test_handle_updates_state_without_streamlit_render_when_placeholder_mocked(self):
        class _Slot:
            def empty(self):
                return None

            def markdown(self, *args, **kwargs):
                return None

        panel = SearchProgressPanel(_Slot())
        panel.handle(STAGE_PREPARE, "start")
        assert panel.state.started
        assert panel.state.bar_percent == 10
        panel.finish()
        assert panel.state.is_complete
        assert panel.state.bar_percent == 100
