"""Streamlit chat UI for the rental search agent. Uses run_agent_step_events from client."""

import json
import logging
from pathlib import Path

import streamlit as st

from rental_search_agent.adapter import SearchBackendError, search
from rental_search_agent.agent import current_date_context, flow_instructions
from rental_search_agent.api_config import has_api_credentials
from rental_search_agent.client import (
    _get_active_search_criteria_from_messages,
    _get_master_listings_from_messages,
    _get_parsed_proximity_rules_from_messages,
    _load_env_file,
    _make_llm_client,
    get_last_rental_search_filters,
    run_agent_step_events,
)
from rental_search_agent.chat_summary import summarize_conversation_for_preferences
from rental_search_agent.display_format import (
    escape_markdown_link_text as _escape_markdown_link_text,
    safe_http_url as _safe_http_url,
)
from rental_search_agent.filtering import filter_listings as do_filter_listings
from rental_search_agent.listing_analysis import analyze_listing_against_preferences
from rental_search_agent.logging_config import clear_run_id, log_stage, new_run_id, set_run_id
from rental_search_agent.models import RentalSearchFilters
from rental_search_agent.preference_apply import (
    apply_search_preferences,
    make_apply_tool_messages,
    make_rental_search_tool_messages,
    prepare_sidebar_search,
    structural_prefs_for_rerank,
    with_display_rank,
)
from rental_search_agent.preference_resolution import (
    PREF_KEYS,
    is_placeholder_qualitative,
    merge_chat_over_stored,
    preferences_block as _shared_preferences_block,
    stored_prefs_to_effective,
)
from rental_search_agent.preference_store import (
    LOCAL_USER_ID,
    FilePreferenceStore,
    default_preferences_file_path,
    normalize_preferences,
)
from rental_search_agent.auth_principal import Principal, current_principal
from rental_search_agent.capability_policy import CapabilityPolicy
from rental_search_agent.session_runtime import (
    clear_runtime,
    get_searches_used,
    load_active_preferences,
    merge_guest_prefs_on_login,
    save_active_preferences,
    set_runtime,
)
from rental_search_agent.proximity_parser import parse_proximity_preferences
from rental_search_agent.streamlit_analysis import render_listing_analysis
from rental_search_agent.streamlit_results import (
    _analyze_button_key,
    _build_map_data,
    _format_bedrooms,
    _format_days_on_market,
    _format_listing_price,
    _format_map_price_label,
    _format_match_score,
    _listings_to_table_rows,
    listing_match_score,
    normalize_map_label_mode,
    normalize_results_view,
    render_search_results,
)

logger = logging.getLogger(__name__)


def _preferences_block(prefs: dict) -> str:
    """Build the search-relevant preferences block to inject into the system message."""
    return _shared_preferences_block(prefs)


def _preferences_file() -> Path:
    """Path to optional JSON file for persisting preferences across sessions."""
    return default_preferences_file_path()


def _load_preferences_from_file() -> dict:
    """Load preferences for tests / local file path injection."""
    return FilePreferenceStore(_preferences_file()).load(LOCAL_USER_ID)


def _save_preferences_to_file(prefs: dict) -> None:
    """Save preferences for tests / local file path injection."""
    FilePreferenceStore(_preferences_file()).save(LOCAL_USER_ID, prefs)


def _ensure_prefs_dict() -> dict:
    prefs = st.session_state.get("user_preferences")
    if not isinstance(prefs, dict):
        prefs = {k: "" for k in PREF_KEYS}
        st.session_state["user_preferences"] = prefs
    return prefs


def _bind_runtime() -> Principal:
    """Set session_runtime from Streamlit session + current principal."""
    principal = current_principal()
    prefs = _ensure_prefs_dict()
    searches_used = int(st.session_state.get("anon_searches_used") or 0)
    has_results = bool(st.session_state.get("display_list") or st.session_state.get("search_master"))
    set_runtime(
        principal,
        searches_used=searches_used,
        has_results=has_results,
        prefs_holder=prefs,
    )
    return principal


def _sync_searches_from_runtime() -> None:
    st.session_state["anon_searches_used"] = get_searches_used()


def _oidc_logged_in() -> bool:
    try:
        import streamlit as st

        user = getattr(st, "user", None)
        return bool(getattr(user, "is_logged_in", False)) if user is not None else False
    except Exception:
        return False


def _reset_session_for_identity_change() -> None:
    """Clear prefs/listings/chat when leaving an authenticated identity (e.g. sign-out)."""
    st.session_state["user_preferences"] = {k: "" for k in PREF_KEYS}
    st.session_state["display_list"] = []
    st.session_state["master_list"] = []
    st.session_state["search_master"] = []
    st.session_state["display_source"] = None
    st.session_state["last_sort_by"] = None
    st.session_state["apply_warnings"] = []
    st.session_state["analyze_listing_id"] = None
    st.session_state["analyze_listing"] = None
    st.session_state["analysis_result"] = {}
    st.session_state["chat_summary"] = ""
    st.session_state["chat_summary_message_count"] = None
    st.session_state["anon_searches_used"] = 0
    st.session_state["_guest_prefs_hint_shown"] = False
    st.session_state["messages"] = [
        {"role": "system", "content": _build_system_content()},
    ]
    st.session_state["pending_ask"] = None


def _handle_auth_transition(principal: Principal) -> None:
    """On login merge guest prefs; on leaving an authenticated user, clear session data."""
    prev = st.session_state.get("_auth_user_id")
    if principal.is_authenticated:
        if prev != principal.user_id:
            # Switching accounts: do not carry previous user's prefs into merge.
            if prev and prev not in ("local", None) and prev != principal.user_id:
                guest_prefs = {k: "" for k in PREF_KEYS}
            else:
                guest_prefs = dict(_ensure_prefs_dict())
            merged = merge_guest_prefs_on_login(principal, guest_prefs)
            st.session_state["user_preferences"] = dict(merged)
            st.session_state["_auth_user_id"] = principal.user_id
            if st.session_state.get("messages"):
                st.session_state["messages"][0] = {
                    "role": "system",
                    "content": _build_system_content(),
                }
    elif principal.is_dev:
        st.session_state["_auth_user_id"] = "local"
    else:
        # Guest (including after logout / allowlist deny). If we just left an
        # authenticated identity, wipe session so the next user cannot see it.
        if prev and prev not in ("local", None):
            _reset_session_for_identity_change()
        st.session_state["_auth_user_id"] = None


def _render_auth_sidebar(principal: Principal) -> None:
    with st.sidebar:
        st.subheader("Account")
        if principal.is_dev:
            st.caption("Local mode (Google sign-in not configured). Prefs save to this machine.")
        elif principal.is_authenticated:
            label = principal.name or principal.email or "Signed in"
            st.caption(f"Signed in as {label}")
            if st.button("Sign out", key="auth_sign_out"):
                st.logout()
        else:
            if principal.allowlist_denied:
                st.warning(
                    "Your Google account is not on the beta allowlist. "
                    "You can keep using guest mode with limited searches."
                )
            st.caption("Try the tool as a guest, then sign in to save preferences and unlock multi-city search.")
            # Allowlist-denied users are still OIDC-logged-in; offer Sign out.
            if _oidc_logged_in():
                if st.button("Sign out", key="auth_sign_out_denied"):
                    st.logout()
            elif st.button("Sign in with Google", key="auth_sign_in", type="primary"):
                st.login("google")
            rem = CapabilityPolicy(
                principal=principal,
                searches_used=int(st.session_state.get("anon_searches_used") or 0),
            ).remaining_searches()
            if rem is not None:
                st.caption(f"Free searches remaining this session: {rem}")


def _build_system_content() -> str:
    """System message content: current date + flow instructions + current preferences block."""
    prefs = st.session_state.get("user_preferences") or {k: "" for k in PREF_KEYS}
    return current_date_context() + flow_instructions() + "\n\n" + _preferences_block(prefs)


def _sync_preferences_from_file() -> dict:
    """Reload durable/session prefs into session so chat fill-in is visible to UI/Analyze."""
    _bind_runtime()
    loaded = load_active_preferences()
    st.session_state["user_preferences"] = dict(loaded)
    if st.session_state.get("messages"):
        st.session_state["messages"][0] = {"role": "system", "content": _build_system_content()}
    return loaded


def _escape_markdown_plain(text: str) -> str:
    """Escape markdown metacharacters in scraped/LLM text rendered via st.markdown."""
    out = str(text)
    for ch in ("\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!", "|"):
        out = out.replace(ch, "\\" + ch)
    return out


def _ensure_env_loaded() -> None:
    """Load .env and configure package logging (idempotent)."""
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
    if "anon_searches_used" not in st.session_state:
        st.session_state["anon_searches_used"] = 0
    if "_auth_user_id" not in st.session_state:
        st.session_state["_auth_user_id"] = None
    if "user_preferences" not in st.session_state:
        # Placeholder until principal is bound; filled in run_ui after auth transition.
        st.session_state["user_preferences"] = {k: "" for k in PREF_KEYS}
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
    if "search_master" not in st.session_state:
        st.session_state["search_master"] = []
    if "apply_warnings" not in st.session_state:
        st.session_state["apply_warnings"] = []
    if "display_source" not in st.session_state:
        st.session_state["display_source"] = None
    if "last_sort_by" not in st.session_state:
        st.session_state["last_sort_by"] = None
    if "chat_open" not in st.session_state:
        st.session_state["chat_open"] = True
    if "results_view" not in st.session_state:
        st.session_state["results_view"] = "grid"
    else:
        st.session_state["results_view"] = normalize_results_view(
            st.session_state.get("results_view")
        )
    if "map_label_mode" not in st.session_state:
        st.session_state["map_label_mode"] = "price"
    else:
        st.session_state["map_label_mode"] = normalize_map_label_mode(
            st.session_state.get("map_label_mode")
        )


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
            logger.warning("Proximity preference parse failed", exc_info=True)
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
        logger.warning("Proximity filter safeguard failed; showing unfiltered", exc_info=True)
        return listings


def _apply_default_match_score_sort(listings: list[dict]) -> list[dict]:
    """Display-only: sort by match_score (fallback semantic_score) desc when available.

    Pure Python list sort (no filter_listings round-trip), so each listing's 'rank'
    field is unchanged — displayed order changes, but 'rank' still identifies
    listings for "listing N" references.
    """
    if not any(listing_match_score(item) is not None for item in listings):
        return listings

    def _key(item: dict) -> tuple:
        score = listing_match_score(item)
        if score is None:
            return (1, -1.0)
        return (0, -score)

    return sorted(listings, key=_key)


def _inject_chat_blob_css() -> None:
    """Dock the chat launcher/panel to the viewport.

    Use attribute selectors — Streamlit's st-key-* class may sit on a wrapper.
    Do not paint an opaque fill: results already reserve space, so the panel
    should inherit the app theme background (light and dark).
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
            border: 1px solid rgba(128, 128, 128, 0.28) !important;
            border-radius: 12px !important;
            box-shadow: 0 8px 28px rgba(0, 0, 0, 0.12) !important;
            padding: 0.6rem 0.75rem 0.75rem !important;
            overflow: visible !important;
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
            border: 1px solid rgba(128, 128, 128, 0.28) !important;
            border-radius: 24px !important;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12) !important;
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
    if "search_master" in listing_state:
        st.session_state["search_master"] = listing_state.get("search_master") or []
    elif not st.session_state.get("search_master"):
        st.session_state["search_master"] = _get_master_listings_from_messages(
            st.session_state.get("messages") or []
        )
    # Chat fill-in writes preferences.json; keep session + system prompt in sync.
    _sync_preferences_from_file()


def _run_user_prompt(client, model, prompt: str) -> None:
    """Append a user message, run one agent step, and rerun."""
    _bind_runtime()
    st.session_state["messages"].append({"role": "user", "content": prompt})
    payload, listing_state = _run_agent_step_with_ui(client, model)
    _sync_searches_from_runtime()
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
    _bind_runtime()
    step_placeholders: dict[int, "st.delta_generator.DeltaGenerator"] = {}
    final_event: dict | None = None
    with st.chat_message("assistant"):
        status_box = st.status("Working...", expanded=True)
        text_placeholder = st.empty()
        acc_text = ""
        try:
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
                    # Keep guest search counter in sync after rental_search.
                    _sync_searches_from_runtime()
                    _bind_runtime()
                elif etype == "text_delta":
                    acc_text += event["delta"]
                    text_placeholder.markdown(acc_text)
                elif etype == "done":
                    final_event = event
        except Exception:
            logger.exception("Agent step failed")
            status_box.update(label="Error", state="error", expanded=True)
            raise
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


def _search_master_listings() -> list[dict]:
    """Original rental_search corpus: session cache, else recover from chat history."""
    cached = st.session_state.get("search_master") or []
    if cached:
        return cached
    return _get_master_listings_from_messages(st.session_state.get("messages") or [])


def _commit_preferences(new_prefs: dict) -> None:
    """Persist sidebar prefs (durable for auth/dev; session-only for guests) and refresh system prompt."""
    principal = _bind_runtime()
    normalized = normalize_preferences(new_prefs)
    st.session_state["user_preferences"] = normalized
    save_active_preferences(normalized)
    if principal.is_guest:
        st.session_state.setdefault("apply_warnings", [])
        # Soft hint once per save — keep short.
        hint = "Guest mode: preferences are saved for this browser session only. Sign in to keep them."
        if hint not in (st.session_state.get("apply_warnings") or []):
            # Don't spam warnings list on every save; use a session flag.
            if not st.session_state.get("_guest_prefs_hint_shown"):
                st.session_state["_guest_prefs_hint_shown"] = True
                st.session_state["apply_warnings"] = list(
                    st.session_state.get("apply_warnings") or []
                ) + [hint]
    messages = st.session_state.get("messages") or []
    if messages:
        messages[0] = {"role": "system", "content": _build_system_content()}
        st.session_state["messages"] = messages


def _apply_pipeline_to_session(result, *, search_master: list[dict] | None = None) -> None:
    """Write pipeline output into session listing state and message history."""
    if search_master is not None:
        st.session_state["search_master"] = search_master
    st.session_state["display_list"] = result.listings
    st.session_state["master_list"] = result.listings
    st.session_state["display_source"] = result.display_source
    st.session_state["last_sort_by"] = result.last_sort_by
    st.session_state["apply_warnings"] = list(result.warnings or [])
    if result.applied:
        st.session_state["messages"] = list(st.session_state.get("messages") or []) + make_apply_tool_messages(
            result
        )


def _run_sidebar_search(new_prefs: dict, previous_prefs: dict) -> None:
    """Save already done. Scrape when needed, otherwise re-rank the current master list."""
    principal = _bind_runtime()
    policy = CapabilityPolicy(
        principal=principal,
        searches_used=int(st.session_state.get("anon_searches_used") or 0),
        has_results=bool(st.session_state.get("display_list") or st.session_state.get("search_master")),
    )
    search_master = _search_master_listings()
    messages = st.session_state.get("messages") or []
    request = prepare_sidebar_search(
        new_prefs,
        previous_prefs,
        has_master=bool(search_master),
        last_filters=get_last_rental_search_filters(messages),
    )
    if request.kind == "error":
        st.session_state["apply_warnings"] = request.warnings
        logger.warning("Sidebar search rejected: %s", request.warnings)
        return
    if request.kind == "scrape" and not policy.can_scrape():
        st.session_state["apply_warnings"] = [policy.scrape_denied_message()]
        return
    set_run_id(new_run_id())
    try:
        if request.kind == "scrape":
            logger.info("Sidebar search start kind=scrape")
            try:
                filters = RentalSearchFilters.model_validate(request.filters)
            except Exception as e:
                logger.warning("Sidebar search invalid filters: %s", e, exc_info=True)
                st.session_state["apply_warnings"] = [f"Could not build search filters: {e}"]
                return
            if len(filters.location_list()) > 1 and not policy.can_multi_city():
                st.session_state["apply_warnings"] = [policy.multi_city_denied_message()]
                return
            # Charge guest credit on scrape attempt (mirrors rental_search tool).
            used = policy.record_scrape()
            st.session_state["anon_searches_used"] = used
            set_runtime(
                principal,
                searches_used=used,
                has_results=bool(st.session_state.get("display_list") or st.session_state.get("search_master")),
                prefs_holder=_ensure_prefs_dict(),
            )
            try:
                with log_stage(logger, "sidebar_scrape"):
                    resp = search(filters)
            except SearchBackendError as e:
                logger.warning("Sidebar search backend failure: %s", e)
                st.session_state["apply_warnings"] = [str(e)]
                return
            except Exception as e:
                logger.warning("Sidebar search failed: %s", e, exc_info=True)
                st.session_state["apply_warnings"] = [f"Search failed: {e}"]
                return
            set_runtime(
                principal,
                searches_used=used,
                has_results=True,
                prefs_holder=_ensure_prefs_dict(),
            )
            data = resp.model_dump()
            listings = with_display_rank(data.get("listings") or [])
            data["listings"] = listings
            st.session_state["messages"] = list(messages) + make_rental_search_tool_messages(
                request.filters or {}, data
            )
            effective = stored_prefs_to_effective(new_prefs)
            result = apply_search_preferences(listings, effective)
            if result.warnings or result.skipped:
                logger.warning(
                    "Sidebar scrape apply soft failures warnings=%s skipped=%s",
                    result.warnings,
                    result.skipped,
                )
            _apply_pipeline_to_session(result, search_master=listings)
            logger.info(
                "Sidebar search end kind=scrape total_count=%s display=%s",
                data.get("total_count"),
                len(result.listings or []),
            )
            return

        logger.info("Sidebar search start kind=rerank master=%d", len(search_master))
        search_criteria = _get_active_search_criteria_from_messages(messages)
        structural_prefs = structural_prefs_for_rerank(new_prefs, search_criteria)
        score_prefs = stored_prefs_to_effective(new_prefs)
        with log_stage(logger, "sidebar_rerank", master=len(search_master)):
            result = apply_search_preferences(
                search_master,
                score_prefs,
                structural_prefs=structural_prefs,
            )
        if result.warnings or result.skipped:
            logger.warning(
                "Sidebar rerank soft failures warnings=%s skipped=%s",
                result.warnings,
                result.skipped,
            )
        _apply_pipeline_to_session(result)
        logger.info(
            "Sidebar search end kind=rerank display=%s applied=%s",
            len(result.listings or []),
            result.applied,
        )
    finally:
        clear_run_id()


def _render_preferences_sidebar() -> None:
    """Sidebar Search Preferences form. Contact/viewing fields stay persisted but hidden."""
    prefs = st.session_state.get("user_preferences") or {k: "" for k in PREF_KEYS}
    principal = current_principal()
    with st.sidebar:
        st.subheader("Search Preferences")
        st.caption(
            "Used as defaults for search, filtering, and match scoring. "
            "Criteria stated in chat override these for that search. "
            "Empty fields may be filled from chat when you search. "
            "Search scrapes when location, buy/rent, or structural fields change, "
            "or when there are no results yet; otherwise it re-ranks."
        )
        if principal.is_guest:
            st.info(
                "Guest mode: preferences are session-only. "
                "Sign in to save them. Multi-city metro search requires sign-in."
            )
        for warning in st.session_state.get("apply_warnings") or []:
            st.warning(warning)
        with st.form("preferences_form"):
            location = st.text_input(
                "Location",
                value=prefs.get("location", ""),
                placeholder="e.g. Vancouver, BC"
                + ("" if principal.is_guest else " or Metro Vancouver"),
                key="pref_location",
            )
            if principal.is_guest:
                st.caption("Tip: use a single city. Metro multi-city search unlocks after sign-in.")
            saved_listing_type = str(prefs.get("listing_type") or "").strip()
            listing_choice = st.segmented_control(
                "Buy / Rent",
                options=["Rent", "Buy"],
                default="Buy" if saved_listing_type == "for_sale" else "Rent",
                key="pref_listing_mode",
            )
            budget = st.text_input(
                "Budget max (CAD)",
                value=prefs.get("budget_max", ""),
                placeholder="e.g. 2800",
                key="pref_budget_max",
            )
            col_beds = st.columns(2)
            with col_beds[0]:
                min_beds = st.text_input(
                    "Beds min",
                    value=prefs.get("min_bedrooms", ""),
                    placeholder="e.g. 2",
                    key="pref_min_bedrooms",
                )
            with col_beds[1]:
                max_beds = st.text_input(
                    "Beds max",
                    value=prefs.get("max_bedrooms", ""),
                    placeholder="optional",
                    key="pref_max_bedrooms",
                )
            min_baths = st.text_input(
                "Baths min",
                value=prefs.get("min_bathrooms", ""),
                placeholder="e.g. 1.5",
                key="pref_min_bathrooms",
            )
            require_den = st.checkbox(
                "Require den",
                value=str(prefs.get("require_den") or "").strip().lower() in ("1", "true", "yes", "y", "on"),
                key="pref_require_den",
            )
            min_sqft = st.text_input(
                "Size min (sqft)",
                value=prefs.get("min_sqft", ""),
                placeholder="e.g. 700",
                key="pref_min_sqft",
            )
            proximity = st.text_area(
                "Proximity preferences",
                value=prefs.get("proximity_preferences", ""),
                placeholder="e.g. max 30 min drive to downtown",
                key="pref_proximity",
            )
            qualitative = st.text_area(
                "Listing preferences",
                value=prefs.get("qualitative_preferences", ""),
                placeholder="e.g. balcony, parking, gym, pet-friendly",
                key="pref_qualitative",
            )
            btn_cols = st.columns(2)
            with btn_cols[0]:
                saved = st.form_submit_button("Save")
            with btn_cols[1]:
                searched = st.form_submit_button("Search")
            if saved or searched:
                new_prefs = {k: str(prefs.get(k, "") or "") for k in PREF_KEYS}
                new_prefs.update(
                    {
                        "location": (location or "").strip(),
                        "listing_type": (
                            "for_sale" if listing_choice == "Buy" else "for_rent"
                        ),
                        "budget_max": (budget or "").strip(),
                        "min_bedrooms": (min_beds or "").strip(),
                        "max_bedrooms": (max_beds or "").strip(),
                        "min_bathrooms": (min_baths or "").strip(),
                        "require_den": "true" if require_den else "",
                        "min_sqft": (min_sqft or "").strip(),
                        "proximity_preferences": (proximity or "").strip(),
                        "qualitative_preferences": (qualitative or "").strip(),
                    }
                )
                previous = dict(prefs)
                _commit_preferences(new_prefs)
                if searched:
                    request = prepare_sidebar_search(
                        new_prefs,
                        previous,
                        has_master=bool(_search_master_listings()),
                        last_filters=get_last_rental_search_filters(
                            st.session_state.get("messages") or []
                        ),
                    )
                    if request.kind == "error":
                        st.session_state["apply_warnings"] = request.warnings
                    else:
                        with st.spinner(request.spinner):
                            _run_sidebar_search(new_prefs, previous)
                else:
                    # Keep guest hint if present; clear other scrape warnings.
                    if not principal.is_guest:
                        st.session_state["apply_warnings"] = []
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
                _bind_runtime()
                payload, listing_state = _run_agent_step_with_ui(client, model)
                _sync_searches_from_runtime()
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
    from rental_search_agent.logging_config import configure_logging

    configure_logging()
    st.set_page_config(page_title="Property Search Assistant", page_icon="🏠", layout="wide")
    _ensure_env_loaded()
    _init_session_state()
    try:
        _main_body()
    finally:
        clear_runtime()


def _main_body() -> None:
    principal = _bind_runtime()
    _handle_auth_transition(principal)
    principal = _bind_runtime()
    if principal.is_dev and not st.session_state.get("_dev_prefs_loaded"):
        st.session_state["user_preferences"] = _load_preferences_from_file()
        st.session_state["_dev_prefs_loaded"] = True
        st.session_state["messages"][0] = {"role": "system", "content": _build_system_content()}
        principal = _bind_runtime()
    _inject_chat_blob_css()
    st.title("Property Search Assistant")

    _render_auth_sidebar(principal)
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
    if last_sort_by is None or last_sort_by in ("semantic_score", "match_score"):
        listings = _apply_default_match_score_sort(listings)

    # Analysis card at top: when user clicked Analyze, run analysis and show result
    # before the search results so the detail view is immediately visible.
    analyze_listing_id = st.session_state.get("analyze_listing_id")
    analyze_listing = st.session_state.get("analyze_listing")
    analysis_result = st.session_state.get("analysis_result", {})
    if analyze_listing_id and analyze_listing:
        from rental_search_agent.session_runtime import get_capability_policy as _gcp

        if not _gcp().can_analyze():
            with st.expander("Analysis result", expanded=True):
                st.warning("Run a search first (or sign in) to analyze listings.")
                if st.button("Clear analysis"):
                    st.session_state["analyze_listing_id"] = None
                    st.session_state["analyze_listing"] = None
                    st.rerun()
        else:
            prefs = _sync_preferences_from_file()
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
                # Allow analyze when any score-relevant stored preference exists
                from rental_search_agent.preference_resolution import stored_prefs_to_effective

                if not stored_prefs_to_effective(prefs).has_score_relevant_prefs():
                    with st.expander("Analysis result", expanded=True):
                        st.warning("Set Search Preferences in the sidebar first, then click Analyze again.")
                        if st.button("Clear analysis"):
                            st.session_state["analyze_listing_id"] = None
                            st.session_state["analyze_listing"] = None
                            st.rerun()
                    preferences_text = ""
                else:
                    preferences_text = "Match my search preferences"
            if preferences_text:
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
                            chat_messages = st.session_state.get("messages") or []
                            search_criteria = _get_active_search_criteria_from_messages(chat_messages)
                            proximity_rules = _get_parsed_proximity_rules_from_messages(chat_messages)
                            chat = dict(search_criteria or {})
                            if qualitative and not is_placeholder_qualitative(qualitative):
                                chat["qualitative_preferences"] = qualitative
                            effective = merge_chat_over_stored(prefs, chat)
                            if is_placeholder_qualitative(effective.qualitative_preferences):
                                effective = effective.model_copy(update={"qualitative_preferences": ""})
                            result = analyze_listing_against_preferences(
                                analyze_listing,
                                preferences_text,
                                conversation_context=conversation_context or None,
                                stored_prefs=prefs,
                                chat_criteria=chat,
                                proximity_rules=proximity_rules or [],
                                effective_prefs=effective,
                            )
                            st.session_state.setdefault("analysis_result", {})[
                                analyze_listing_id
                            ] = result
                        except Exception as e:
                            logger.warning(
                                "Listing analysis failed listing_id=%s: %s",
                                analyze_listing_id,
                                e,
                                exc_info=True,
                            )
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
                            render_listing_analysis(analyze_listing, result)
                            if st.button("Clear analysis"):
                                st.session_state["analyze_listing_id"] = None
                                st.session_state["analyze_listing"] = None
                                st.rerun()

    if listings:
        render_search_results(listings)
    else:
        st.caption("Enter location and beds in Search Preferences, then click Search.")

    _render_chat_panel(client, model)
    _sync_searches_from_runtime()


def run_ui() -> None:
    """Entry point for rental-search-ui script: start Streamlit server."""
    import sys
    import streamlit.web.cli as st_cli
    app_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(app_path), "--server.headless", "true"]
    st_cli.main()


if __name__ == "__main__":
    main()
