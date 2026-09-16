"""Canonical search-pipeline progress events and a Streamlit-free view model.

Domain code emits ``(name, phase, ok, listing_count)`` events. Streamlit (or
tests) reduce those events into a five-stage panel. No Streamlit imports here.
"""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., None]

STAGE_PREPARE = "prepare_search"
STAGE_SEARCH = "search_and_validate"
STAGE_CALC_PROX = "calculate_proximity"
STAGE_APPLY_PROX = "apply_proximity_criteria"
STAGE_SCORE = "score_and_rank"
WORKFLOW_COMPLETE = "workflow_complete"

STAGE_ORDER: tuple[str, ...] = (
    STAGE_PREPARE,
    STAGE_SEARCH,
    STAGE_CALC_PROX,
    STAGE_APPLY_PROX,
    STAGE_SCORE,
)

PROGRESS_PERCENT: dict[str, int] = {
    STAGE_PREPARE: 10,
    STAGE_SEARCH: 30,
    STAGE_CALC_PROX: 50,
    STAGE_APPLY_PROX: 70,
    STAGE_SCORE: 90,
    WORKFLOW_COMPLETE: 100,
}

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_COMPLETE = "complete"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

PHASE_START = "start"
PHASE_END = "end"
PHASE_SKIP = "skip"

# Tools that mean a search workflow is underway (chat path).
SEARCH_WORKFLOW_TOOLS = frozenset(
    {
        "expand_search_region",
        "rental_search",
        "apply_search_preferences",
        "parse_proximity_preferences",
        "geocode_location",
        "geocode_proximity_references",
        "enrich_listings_with_proximity",
        "filter_listings",
        "score_listings_by_preferences",
    }
)
PREPARE_TOOLS = frozenset(
    {
        "expand_search_region",
        "geocode_location",
        "geocode_proximity_references",
        "parse_proximity_preferences",
    }
)


@dataclass(frozen=True)
class StageCopy:
    """Static copy for one pipeline stage."""

    section: str
    title: str
    operation: str
    description: str
    stepper_label: str
    progress_percent: int
    footer_secondary: str | None = None


STAGE_COPY: dict[str, StageCopy] = {
    STAGE_PREPARE: StageCopy(
        section="1 · Preparing search",
        title="Preparing your search",
        operation="Building search filters",
        description=(
            "Validating location, listing type, budget, bedrooms, bathrooms, and size"
        ),
        stepper_label="Preparing search",
        progress_percent=10,
    ),
    STAGE_SEARCH: StageCopy(
        section="2 · Searching and validating listings",
        title="Finding matching properties",
        operation="Searching and validating active listings",
        description=(
            "Applying structural filters at the source and checking returned listing data"
        ),
        stepper_label="Search & validate",
        progress_percent=30,
    ),
    STAGE_CALC_PROX: StageCopy(
        section="3 · Calculating proximity",
        title="Calculating location fit",
        operation="Calculating travel times for {validated_count} listings",
        description="Using your proximity rules and available listing coordinates",
        stepper_label="Calculate proximity",
        progress_percent=50,
    ),
    STAGE_APPLY_PROX: StageCopy(
        section="4 · Applying proximity criteria",
        title="Filtering by location fit",
        operation="Applying your travel-time limits",
        description=(
            "Checking listings with available travel-time results against every "
            "proximity rule"
        ),
        stepper_label="Apply proximity criteria",
        progress_percent=70,
    ),
    STAGE_SCORE: StageCopy(
        section="5 · Scoring and ranking matches",
        title="Scoring and ranking matches",
        operation="Calculating final match scores",
        description=(
            "Combining structural, proximity, amenity, and semantic evidence "
            "for {remaining_count} matches"
        ),
        stepper_label="Score & rank",
        progress_percent=90,
        footer_secondary="Almost done",
    ),
}

# Backward-compatible aliases from earlier coarse pipeline events.
EVENT_ALIASES: dict[str, str] = {
    "filter_listings": STAGE_SEARCH,
    "rental_search": STAGE_SEARCH,
    "enrich_listings_with_proximity": STAGE_CALC_PROX,
    "score_listings_by_preferences": STAGE_SCORE,
}

_SKIP_DETAIL = "Not needed"
_FAIL_DETAIL = "Unsuccessful"


def invoke_progress(
    progress: ProgressCallback | None,
    name: str,
    phase: str,
    ok: bool = True,
    listing_count: int | None = None,
) -> None:
    """Call a 3- or 4-arg progress callback; never raise to the pipeline."""
    if progress is None:
        return
    try:
        progress(name, phase, ok, listing_count)
        return
    except TypeError:
        pass
    except Exception:
        logger.debug("search progress callback failed", exc_info=True)
        return
    try:
        progress(name, phase, ok)
    except Exception:
        logger.debug("search progress callback failed", exc_info=True)


def canonical_stage_name(name: str) -> str | None:
    """Map an emitted event name to a UI stage, or None if it is not a stage."""
    if name in STAGE_COPY or name == WORKFLOW_COMPLETE:
        return name
    return EVENT_ALIASES.get(name)


def format_elapsed(seconds: int) -> str:
    """``Elapsed: 18 sec`` — no numeric percent, no 'Conditional'."""
    n = max(0, int(seconds))
    return f"Elapsed: {n} sec"


def _format_template(template: str, values: Mapping[str, Any]) -> str:
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template.split("{", 1)[0].rstrip()


@dataclass
class StageState:
    status: str = STATUS_PENDING
    listing_count: int | None = None


@dataclass(frozen=True)
class StageView:
    id: str
    section: str
    title: str
    operation: str
    description: str
    stepper_label: str
    stepper_detail: str | None
    status: str
    progress_percent: int
    footer_secondary: str | None = None


@dataclass(frozen=True)
class ProgressView:
    stages: tuple[StageView, ...]
    active: StageView
    bar_percent: int
    elapsed_seconds: int
    started_at_ms: int
    footer_secondary: str | None
    is_complete: bool
    started: bool


@dataclass
class SearchProgressState:
    """Reduce pipeline events into the five-row progress view."""

    _statuses: dict[str, StageState] = field(
        default_factory=lambda: {sid: StageState() for sid in STAGE_ORDER}
    )
    _active_id: str = STAGE_PREPARE
    _bar_percent: int = 0
    _started: bool = False
    _complete: bool = False
    _validated_count: int | None = None
    _remaining_count: int | None = None
    _t0_mono: float = field(default_factory=time.monotonic)
    _t0_wall_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def handle(
        self,
        name: str,
        phase: str,
        ok: bool = True,
        listing_count: int | None = None,
    ) -> ProgressView:
        stage_id = canonical_stage_name(name)
        if stage_id is None:
            return self.view()
        self._started = True
        if stage_id == WORKFLOW_COMPLETE:
            self.complete()
            return self.view()

        idx = STAGE_ORDER.index(stage_id)
        if phase == PHASE_START:
            self._auto_skip_gaps(idx)
            self._complete_prior_active(idx)
            self._statuses[stage_id].status = STATUS_ACTIVE
            self._active_id = stage_id
            self._bar_percent = PROGRESS_PERCENT[stage_id]
            if listing_count is not None:
                self._record_count(stage_id, listing_count, completed=False)
        elif phase == PHASE_SKIP:
            self._statuses[stage_id].status = STATUS_SKIPPED
            if self._active_id == stage_id:
                self._advance_active_after(idx)
        elif phase == PHASE_END:
            self._statuses[stage_id].status = STATUS_COMPLETE if ok else STATUS_FAILED
            if listing_count is not None:
                self._record_count(stage_id, listing_count, completed=True)
                self._statuses[stage_id].listing_count = int(listing_count)
            if self._active_id == stage_id:
                self._advance_active_after(idx)
        return self.view()

    def complete(self) -> ProgressView:
        self._started = True
        self._complete = True
        self._bar_percent = PROGRESS_PERCENT[WORKFLOW_COMPLETE]
        for sid, state in self._statuses.items():
            if state.status == STATUS_ACTIVE:
                state.status = STATUS_COMPLETE
            elif state.status == STATUS_PENDING:
                # Unmentioned trailing stages are not claimed as done.
                if STAGE_ORDER.index(sid) < STAGE_ORDER.index(self._active_id):
                    state.status = STATUS_COMPLETE
        return self.view()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def is_complete(self) -> bool:
        return self._complete

    @property
    def bar_percent(self) -> int:
        return self._bar_percent

    def stage_status(self, stage_id: str) -> str:
        return self._statuses[stage_id].status

    def stage_count(self, stage_id: str) -> int | None:
        return self._statuses[stage_id].listing_count

    def view(self) -> ProgressView:
        elapsed = max(0, int(time.monotonic() - self._t0_mono))
        stages = tuple(self._stage_view(sid) for sid in STAGE_ORDER)
        active = next((s for s in stages if s.status == STATUS_ACTIVE), None)
        if active is None:
            active = stages[STAGE_ORDER.index(self._active_id)]
        footer = active.footer_secondary if active.status == STATUS_ACTIVE else None
        return ProgressView(
            stages=stages,
            active=active,
            bar_percent=self._bar_percent,
            elapsed_seconds=elapsed,
            started_at_ms=self._t0_wall_ms,
            footer_secondary=footer,
            is_complete=self._complete,
            started=self._started,
        )

    def _stage_view(self, stage_id: str) -> StageView:
        meta = STAGE_COPY[stage_id]
        state = self._statuses[stage_id]
        values = self._template_values()
        operation = _format_template(meta.operation, values)
        description = _format_template(meta.description, values)
        detail = self._stepper_detail(stage_id, state)
        return StageView(
            id=stage_id,
            section=meta.section,
            title=meta.title,
            operation=operation,
            description=description,
            stepper_label=meta.stepper_label,
            stepper_detail=detail,
            status=state.status,
            progress_percent=meta.progress_percent,
            footer_secondary=meta.footer_secondary,
        )

    def _template_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if self._validated_count is not None:
            values["validated_count"] = self._validated_count
        if self._remaining_count is not None:
            values["remaining_count"] = self._remaining_count
        elif self._validated_count is not None:
            values["remaining_count"] = self._validated_count
        return values

    def _stepper_detail(self, stage_id: str, state: StageState) -> str | None:
        if state.status == STATUS_SKIPPED:
            return _SKIP_DETAIL
        if state.status == STATUS_FAILED:
            return _FAIL_DETAIL
        if state.status != STATUS_COMPLETE or state.listing_count is None:
            return None
        n = int(state.listing_count)
        if stage_id == STAGE_SEARCH:
            return f"{n} listings"
        if stage_id == STAGE_APPLY_PROX:
            return f"{n} matches"
        return None

    def _record_count(self, stage_id: str, listing_count: int, *, completed: bool) -> None:
        n = int(listing_count)
        if stage_id == STAGE_SEARCH and completed:
            self._validated_count = n
            if self._remaining_count is None:
                self._remaining_count = n
        elif stage_id == STAGE_CALC_PROX and not completed:
            if self._validated_count is None:
                self._validated_count = n
        elif stage_id == STAGE_APPLY_PROX and completed:
            self._remaining_count = n
        elif stage_id == STAGE_SCORE and not completed:
            if self._remaining_count is None:
                self._remaining_count = n

    def _auto_skip_gaps(self, target_idx: int) -> None:
        """When a later stage starts, mark unused intermediate stages skipped."""
        for sid in STAGE_ORDER[:target_idx]:
            if self._statuses[sid].status == STATUS_PENDING:
                self._statuses[sid].status = STATUS_SKIPPED

    def _complete_prior_active(self, target_idx: int) -> None:
        for sid in STAGE_ORDER[:target_idx]:
            if self._statuses[sid].status == STATUS_ACTIVE:
                self._statuses[sid].status = STATUS_COMPLETE

    def _advance_active_after(self, idx: int) -> None:
        for sid in STAGE_ORDER[idx + 1 :]:
            if self._statuses[sid].status == STATUS_PENDING:
                self._active_id = sid
                return
        self._active_id = STAGE_ORDER[idx]


class SearchWorkflowEmitter:
    """Chat/tool adapter: start the panel only once a search-related tool runs."""

    def __init__(self, progress: ProgressCallback | None) -> None:
        self._progress = progress
        self.prepare_open = False
        self.search_open = False
        self.workflow_started = False

    def tool_start(self, name: str) -> None:
        if name not in SEARCH_WORKFLOW_TOOLS:
            return
        if name in PREPARE_TOOLS:
            self._ensure_prepare()
            return
        if name == "rental_search":
            self._ensure_prepare()
            self._close_prepare(ok=True)
            if not self.search_open:
                invoke_progress(self._progress, STAGE_SEARCH, PHASE_START)
                self.search_open = True
            return
        if name == "apply_search_preferences":
            self._ensure_prepare()
            self._close_prepare(ok=True)

    def close_search(self, ok: bool = True, listing_count: int | None = None) -> None:
        if self.search_open:
            invoke_progress(
                self._progress, STAGE_SEARCH, PHASE_END, ok=ok, listing_count=listing_count
            )
            self.search_open = False

    def skip_remaining(self, *, from_stage: str | None = None) -> None:
        started = False
        for sid in STAGE_ORDER:
            if from_stage is not None and not started:
                if sid == from_stage:
                    started = True
                continue
            invoke_progress(self._progress, sid, PHASE_SKIP)

    def apply_callback(
        self, name: str, phase: str, ok: bool = True, listing_count: int | None = None
    ) -> None:
        invoke_progress(self._progress, name, phase, ok=ok, listing_count=listing_count)
        if name == STAGE_SEARCH and phase == PHASE_END:
            self.search_open = False

    def finish(self, ok: bool = True) -> None:
        if not self.workflow_started:
            return
        self._close_prepare(ok=ok)
        if self.search_open:
            invoke_progress(self._progress, STAGE_SEARCH, PHASE_END, ok=ok)
            self.search_open = False
        invoke_progress(self._progress, WORKFLOW_COMPLETE, PHASE_END, ok=ok)

    def _ensure_prepare(self) -> None:
        if self.workflow_started:
            return
        self.workflow_started = True
        invoke_progress(self._progress, STAGE_PREPARE, PHASE_START)
        self.prepare_open = True

    def _close_prepare(self, ok: bool = True) -> None:
        if self.prepare_open:
            invoke_progress(self._progress, STAGE_PREPARE, PHASE_END, ok=ok)
            self.prepare_open = False


def stage_event_order(events: Sequence[tuple[Any, ...]]) -> list[str]:
    """Return canonical stage names in the order they first became active/skipped."""
    seen: list[str] = []
    for ev in events:
        name = ev[0]
        stage = canonical_stage_name(str(name))
        if stage is None or stage == WORKFLOW_COMPLETE:
            continue
        if stage not in seen:
            seen.append(stage)
    return seen


def render_progress_html(view: ProgressView) -> str:
    """Inner panel markup. All dynamic text is HTML-escaped."""
    active = view.active
    footer_secondary = view.footer_secondary or ""
    almost = ""
    if footer_secondary:
        almost = (
            f'<span class="rsa-progress-almost">'
            f"{html.escape(footer_secondary)}</span>"
        )
    elapsed = html.escape(format_elapsed(view.elapsed_seconds))
    bar = max(0, min(100, int(view.bar_percent)))
    busy = "true" if not view.is_complete else "false"
    main = (
        f'<div class="rsa-progress-main">'
        f'<div class="rsa-progress-section">{html.escape(active.section)}</div>'
        f'<h2 class="rsa-progress-title">{html.escape(active.title)}</h2>'
        f'<div class="rsa-progress-op">'
        f'<span class="rsa-progress-spinner" aria-hidden="true"></span>'
        f'<span class="rsa-progress-op-text">{html.escape(active.operation)}</span>'
        f"</div>"
        f'<p class="rsa-progress-desc">{html.escape(active.description)}</p>'
        f'<div class="rsa-progress-bar" role="progressbar" aria-valuemin="0" '
        f'aria-valuemax="100" aria-valuenow="{bar}" '
        f'aria-label="Search progress">'
        f'<div class="rsa-progress-bar-fill" style="width:{bar}%"></div>'
        f"</div>"
        f'<div class="rsa-progress-footer">'
        f'<span class="rsa-progress-elapsed" id="rsa-elapsed" '
        f'data-started-at="{int(view.started_at_ms)}">{elapsed}</span>'
        f"{almost}"
        f"</div>"
        f"</div>"
    )
    steps = "".join(_stepper_item_html(stage) for stage in view.stages)
    return (
        f'<div class="rsa-progress-overlay">'
        f'<section class="rsa-progress-panel" role="status" aria-live="polite" '
        f'aria-busy="{busy}">'
        f'<div class="rsa-progress-layout">'
        f"{main}"
        f'<ol class="rsa-progress-stepper" aria-label="Search pipeline stages">'
        f"{steps}"
        f"</ol>"
        f"</div>"
        f"</section>"
        f"</div>"
    )


def _stepper_item_html(stage: StageView) -> str:
    label = stage.stepper_label
    if stage.stepper_detail:
        label = f"{label} · {stage.stepper_detail}"
    current = ' aria-current="step"' if stage.status == STATUS_ACTIVE else ""
    status_word = {
        STATUS_ACTIVE: "in progress",
        STATUS_COMPLETE: "completed",
        STATUS_SKIPPED: "not needed",
        STATUS_FAILED: "unsuccessful",
        STATUS_PENDING: "pending",
    }.get(stage.status, stage.status)
    return (
        f'<li class="rsa-progress-step is-{html.escape(stage.status)}"{current} '
        f'aria-label="{html.escape(label)} ({status_word})">'
        f'<span class="rsa-step-icon" aria-hidden="true">{_step_icon(stage.status)}</span>'
        f'<span class="rsa-step-label">{html.escape(label)}</span>'
        f"</li>"
    )


def _step_icon(status: str) -> str:
    if status == STATUS_COMPLETE:
        return (
            '<svg class="rsa-step-check" viewBox="0 0 16 16" width="12" height="12">'
            '<path fill="currentColor" d="M6.2 11.4 2.8 8l1.1-1.1 2.3 2.3 5.9-5.9L13.2 4z"/>'
            "</svg>"
        )
    if status == STATUS_FAILED:
        return (
            '<svg class="rsa-step-fail" viewBox="0 0 16 16" width="11" height="11">'
            '<path fill="currentColor" d="M4.2 3.1 3.1 4.2 6.9 8l-3.8 3.8 1.1 1.1L8 9.1l3.8 '
            '3.8 1.1-1.1L9.1 8l3.8-3.8-1.1-1.1L8 6.9z"/>'
            "</svg>"
        )
    return ""


PROGRESS_CSS = """
.rsa-progress-overlay {
    box-sizing: border-box;
    position: fixed;
    top: 3.5rem;
    bottom: 0;
    /* Cap insets so sidebar + chat gutters cannot collapse the overlay. */
    left: min(21rem, 28vw);
    right: min(calc(0.75rem + 420px), 32vw);
    z-index: 9990;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding: 3.25rem 1.5rem 2rem;
    background: #0e1116;
    opacity: 1;
    overflow: auto;
}
.rsa-progress-panel,
.rsa-progress-panel h2,
.rsa-progress-panel p,
.rsa-progress-panel ol,
.rsa-progress-panel li,
.rsa-progress-panel span {
    font-family: var(--font, inherit) !important;
}
.rsa-progress-panel {
    box-sizing: border-box;
    width: min(52rem, 100%);
    max-width: 100%;
    margin: 0 auto;
    padding: 1.2rem 1.35rem 1.1rem;
    color: #e7eef6;
    background: #151c28;
    opacity: 1;
    border: 1px solid #3d4a5c;
    border-radius: 14px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55);
}
.rsa-progress-layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(13.2rem, 15.2rem);
    gap: 1.1rem 1.6rem;
    align-items: center;
}
.rsa-progress-section {
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    color: #8aa0b8;
    margin: 0 0 0.35rem;
}
.rsa-progress-panel h2.rsa-progress-title,
.rsa-progress-title {
    margin: 0 0 0.7rem;
    padding: 0;
    font-size: 1.28rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em;
    line-height: 1.2;
    color: #f3f7fb;
}
.rsa-progress-op {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    font-size: 0.95rem;
    font-weight: 600;
    color: #e8eef5;
    margin: 0 0 0.28rem;
}
.rsa-progress-spinner {
    flex: 0 0 auto;
    width: 1.05rem;
    height: 1.05rem;
    border: 2.4px solid rgba(46, 233, 198, 0.22);
    border-top-color: #2ee9c6;
    border-radius: 50%;
    animation: rsa-progress-spin 0.75s linear infinite;
}
.rsa-progress-panel p.rsa-progress-desc,
.rsa-progress-desc {
    margin: 0 0 0.85rem;
    padding: 0;
    font-size: 0.86rem;
    line-height: 1.4;
    color: #8b9bb0;
}
.rsa-progress-bar {
    height: 0.38rem;
    border-radius: 999px;
    background: #2a3444;
    overflow: hidden;
}
.rsa-progress-bar-fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, #2ee9c6, #5ef0d6);
    box-shadow: 0 0 10px rgba(46, 233, 198, 0.35);
}
.rsa-progress-footer {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.75rem;
    margin-top: 0.7rem;
    font-size: 0.8rem;
    color: #8b9bb0;
}
.rsa-progress-almost {
    color: #9aa9bc;
}
.rsa-progress-stepper {
    list-style: none;
    margin: 0;
    padding: 0.15rem 0 0.15rem 0.15rem;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
}
.rsa-progress-step {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    font-size: 0.84rem;
    color: #7f8fa3;
    line-height: 1.25;
}
.rsa-step-icon {
    flex: 0 0 auto;
    width: 1.05rem;
    height: 1.05rem;
    border-radius: 50%;
    border: 2px solid #4b5870;
    box-sizing: border-box;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}
.rsa-progress-step.is-active {
    color: #dce6f0;
    font-weight: 650;
}
.rsa-progress-step.is-active .rsa-step-icon {
    border-color: #2ee9c6;
    box-shadow: 0 0 0 3px rgba(46, 233, 198, 0.12);
}
.rsa-progress-step.is-complete {
    color: #c5d0dc;
}
.rsa-progress-step.is-complete .rsa-step-icon {
    border-color: #3ee07a;
    background: #3ee07a;
    color: #0d1a12;
}
.rsa-progress-step.is-skipped {
    color: #66768a;
}
.rsa-progress-step.is-failed {
    color: #a8b0bc;
}
.rsa-progress-step.is-failed .rsa-step-icon {
    border-color: #8b93a0;
    color: #c5cad3;
}
.rsa-step-check, .rsa-step-fail { display: block; }
@keyframes rsa-progress-spin {
    to { transform: rotate(360deg); }
}
@media (max-width: 640px) {
    .rsa-progress-layout { grid-template-columns: 1fr; }
    .rsa-progress-stepper { padding-top: 0.35rem; }
}
"""


def build_progress_document(view: ProgressView) -> str:
    """Self-contained HTML document for an iframe (live elapsed JS)."""
    body = render_progress_html(view)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>html,body{{margin:0;padding:0;background:transparent;}}"
        f"{PROGRESS_CSS}</style></head><body>"
        f"{body}"
        "<script>"
        "(function(){"
        "var el=document.getElementById('rsa-elapsed');"
        "if(!el)return;"
        "var start=Number(el.getAttribute('data-started-at'))||Date.now();"
        "function tick(){"
        "var sec=Math.max(0,Math.floor((Date.now()-start)/1000));"
        "el.textContent='Elapsed: '+sec+' sec';"
        "}"
        "tick();"
        "setInterval(tick,250);"
        "})();"
        "</script></body></html>"
    )


def events_include_separate_proximity_stages(events: Iterable[tuple[Any, ...]]) -> bool:
    names = [canonical_stage_name(str(ev[0])) for ev in events]
    return STAGE_CALC_PROX in names and STAGE_APPLY_PROX in names
