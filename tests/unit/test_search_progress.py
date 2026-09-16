"""Unit tests for canonical search-progress events and the HTML view model."""

import html as html_lib
import re

from rental_search_agent.search_progress import (
    PROGRESS_PERCENT,
    STAGE_APPLY_PROX,
    STAGE_CALC_PROX,
    STAGE_ORDER,
    STAGE_PREPARE,
    STAGE_SCORE,
    STAGE_SEARCH,
    STATUS_SKIPPED,
    SearchProgressState,
    SearchWorkflowEmitter,
    WORKFLOW_COMPLETE,
    build_progress_document,
    events_include_separate_proximity_stages,
    format_elapsed,
    render_progress_html,
    stage_event_order,
)


def _collect():
    events: list[tuple] = []

    def _cb(name, phase, ok=True, listing_count=None):
        events.append((name, phase, ok, listing_count))

    return events, _cb


def _drive_full_proximity(state: SearchProgressState) -> SearchProgressState:
    state.handle(STAGE_PREPARE, "start")
    state.handle(STAGE_PREPARE, "end")
    state.handle(STAGE_SEARCH, "start")
    state.handle(STAGE_SEARCH, "end", True, 82)
    state.handle(STAGE_CALC_PROX, "start", True, 82)
    state.handle(STAGE_CALC_PROX, "end", True, 82)
    state.handle(STAGE_APPLY_PROX, "start")
    state.handle(STAGE_APPLY_PROX, "end", True, 68)
    state.handle(STAGE_SCORE, "start", True, 68)
    return state


class TestStageOrderAndPercents:
    def test_full_search_with_proximity_order(self):
        events, cb = _collect()
        for name in STAGE_ORDER:
            cb(name, "start")
            cb(name, "end", True, 10 if name != STAGE_APPLY_PROX else 7)
        assert stage_event_order(events) == list(STAGE_ORDER)
        assert events_include_separate_proximity_stages(events)

    def test_order_without_proximity_skips_middle_stages(self):
        events, cb = _collect()
        cb(STAGE_PREPARE, "start")
        cb(STAGE_PREPARE, "end")
        cb(STAGE_SEARCH, "start")
        cb(STAGE_SEARCH, "end", True, 20)
        cb(STAGE_CALC_PROX, "skip")
        cb(STAGE_APPLY_PROX, "skip")
        cb(STAGE_SCORE, "start", True, 20)
        cb(STAGE_SCORE, "end")
        assert stage_event_order(events) == list(STAGE_ORDER)
        skip_names = [e[0] for e in events if e[1] == "skip"]
        assert skip_names == [STAGE_CALC_PROX, STAGE_APPLY_PROX]

    def test_progress_values(self):
        state = SearchProgressState()
        assert state.bar_percent == 0
        state.handle(STAGE_PREPARE, "start")
        assert state.bar_percent == 10
        state.handle(STAGE_PREPARE, "end")
        state.handle(STAGE_SEARCH, "start")
        assert state.bar_percent == 30
        state.handle(STAGE_SEARCH, "end", True, 5)
        state.handle(STAGE_CALC_PROX, "start")
        assert state.bar_percent == 50
        state.handle(STAGE_CALC_PROX, "end")
        state.handle(STAGE_APPLY_PROX, "start")
        assert state.bar_percent == 70
        state.handle(STAGE_APPLY_PROX, "end", True, 3)
        state.handle(STAGE_SCORE, "start")
        assert state.bar_percent == 90
        state.complete()
        assert state.bar_percent == 100
        assert PROGRESS_PERCENT[STAGE_PREPARE] == 10
        assert PROGRESS_PERCENT[STAGE_SEARCH] == 30
        assert PROGRESS_PERCENT[STAGE_CALC_PROX] == 50
        assert PROGRESS_PERCENT[STAGE_APPLY_PROX] == 70
        assert PROGRESS_PERCENT[STAGE_SCORE] == 90
        assert PROGRESS_PERCENT[WORKFLOW_COMPLETE] == 100

    def test_no_proximity_jumps_from_30_to_90(self):
        state = SearchProgressState()
        state.handle(STAGE_PREPARE, "start")
        state.handle(STAGE_PREPARE, "end")
        state.handle(STAGE_SEARCH, "start")
        state.handle(STAGE_SEARCH, "end", True, 12)
        assert state.bar_percent == 30
        state.handle(STAGE_CALC_PROX, "skip")
        state.handle(STAGE_APPLY_PROX, "skip")
        state.handle(STAGE_SCORE, "start", True, 12)
        assert state.bar_percent == 90
        assert state.stage_status(STAGE_CALC_PROX) == STATUS_SKIPPED
        assert state.stage_status(STAGE_APPLY_PROX) == STATUS_SKIPPED

    def test_auto_skip_when_score_starts_after_search(self):
        state = SearchProgressState()
        state.handle(STAGE_SEARCH, "start")
        state.handle(STAGE_SEARCH, "end", True, 4)
        state.handle(STAGE_SCORE, "start")
        assert state.stage_status(STAGE_PREPARE) == STATUS_SKIPPED
        assert state.stage_status(STAGE_CALC_PROX) == STATUS_SKIPPED
        assert state.stage_status(STAGE_APPLY_PROX) == STATUS_SKIPPED
        assert state.bar_percent == 90


class TestCountsAfterCompletion:
    def test_counts_appear_only_after_end(self):
        state = SearchProgressState()
        state.handle(STAGE_SEARCH, "start")
        view = state.view()
        search_row = next(s for s in view.stages if s.id == STAGE_SEARCH)
        assert search_row.stepper_detail is None
        assert "listings" not in search_row.stepper_label
        state.handle(STAGE_SEARCH, "end", True, 82)
        search_row = next(s for s in state.view().stages if s.id == STAGE_SEARCH)
        assert search_row.stepper_detail == "82 listings"
        state.handle(STAGE_APPLY_PROX, "start")
        apply_row = next(s for s in state.view().stages if s.id == STAGE_APPLY_PROX)
        assert apply_row.stepper_detail is None
        state.handle(STAGE_APPLY_PROX, "end", True, 68)
        apply_row = next(s for s in state.view().stages if s.id == STAGE_APPLY_PROX)
        assert apply_row.stepper_detail == "68 matches"

    def test_skipped_proximity_says_not_needed(self):
        state = SearchProgressState()
        state.handle(STAGE_CALC_PROX, "skip")
        state.handle(STAGE_APPLY_PROX, "skip")
        view = state.view()
        calc = next(s for s in view.stages if s.id == STAGE_CALC_PROX)
        apply = next(s for s in view.stages if s.id == STAGE_APPLY_PROX)
        assert calc.stepper_detail == "Not needed"
        assert apply.stepper_detail == "Not needed"
        html = render_progress_html(view)
        assert "Conditional" not in html
        assert "Not needed" in html
        assert "rsa-progress-overlay" in html
        assert "rsa-progress-panel" in html


class TestScoringAfterProximitySkip:
    def test_score_stage_runs_after_skip(self):
        state = SearchProgressState()
        state.handle(STAGE_SEARCH, "end", True, 5)
        state.handle(STAGE_CALC_PROX, "skip")
        state.handle(STAGE_APPLY_PROX, "skip")
        state.handle(STAGE_SCORE, "start", True, 5)
        view = state.view()
        assert view.active.id == STAGE_SCORE
        assert "5 matches" in view.active.description
        assert view.bar_percent == 90


class TestSafeRendering:
    def test_escapes_dynamic_text(self):
        state = SearchProgressState()
        state.handle(STAGE_PREPARE, "start")
        view = state.view()
        # Inject hostile copy through a reconstructed view.
        from dataclasses import replace

        evil = replace(
            view.active,
            title='</h2><img src=x onerror=alert(1)><h2>',
            operation="<script>alert(1)</script>",
            description='foo" onclick="alert(1)',
            section="<b>bad</b>",
        )
        evil_view = replace(view, active=evil, stages=(evil,) + view.stages[1:])
        markup = render_progress_html(evil_view)
        assert "<script>" not in markup
        assert "<img src=x onerror=" not in markup
        assert "<b>bad</b>" not in markup
        assert html_lib.escape("<script>alert(1)</script>") in markup
        assert html_lib.escape("</h2><img src=x onerror=alert(1)><h2>") in markup
        assert "Conditional" not in markup

    def test_no_visible_percent_label(self):
        state = _drive_full_proximity(SearchProgressState())
        markup = render_progress_html(state.view())
        visible = re.sub(r'style="width:\d+%"', "", markup)
        assert not re.search(r">\s*\d+%\s*<", visible)
        assert "10%" not in visible
        assert 'aria-valuenow="90"' in markup

    def test_elapsed_format(self):
        assert format_elapsed(18) == "Elapsed: 18 sec"
        assert format_elapsed(1) == "Elapsed: 1 sec"
        doc = build_progress_document(SearchProgressState().handle(STAGE_PREPARE, "start"))
        assert "rsa-elapsed" in doc
        assert "Elapsed:" in doc
        assert "setInterval" in doc

    def test_css_inherits_app_font(self):
        from rental_search_agent.search_progress import PROGRESS_CSS

        assert "var(--font, inherit)" in PROGRESS_CSS
        assert ".rsa-progress-overlay" in PROGRESS_CSS
        assert "background: #0e1116" in PROGRESS_CSS
        assert "min(21rem, 28vw)" in PROGRESS_CSS
        assert "--sidebar-width" not in PROGRESS_CSS


class TestWorkflowEmitter:
    def test_ignores_non_search_chat(self):
        events, cb = _collect()
        emitter = SearchWorkflowEmitter(cb)
        emitter.tool_start("ask_user")
        emitter.finish()
        assert events == []

    def test_rental_search_starts_prepare_then_search(self):
        events, cb = _collect()
        emitter = SearchWorkflowEmitter(cb)
        emitter.tool_start("rental_search")
        names_phases = [(e[0], e[1]) for e in events]
        assert names_phases[0] == (STAGE_PREPARE, "start")
        assert names_phases[1] == (STAGE_PREPARE, "end")
        assert names_phases[2] == (STAGE_SEARCH, "start")
