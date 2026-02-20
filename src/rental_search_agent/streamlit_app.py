"""Streamlit chat UI for the rental search agent. Uses run_agent_step from client."""

import json
import os
from pathlib import Path

import streamlit as st

from rental_search_agent.agent import flow_instructions
from rental_search_agent.client import _load_env_file, _make_llm_client, run_agent_step


def _ensure_env_loaded() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent
    _load_env_file(project_root / ".env")


def _get_client_and_model():
    """Return (client, model), cached in session state. Ensures env is loaded first."""
    if "llm_client" in st.session_state and "llm_model" in st.session_state:
        return st.session_state["llm_client"], st.session_state["llm_model"]
    _ensure_env_loaded()
    if not os.environ.get("OPENROUTER_API_KEY", "").strip() and not os.environ.get("OPENAI_API_KEY", "").strip():
        return None, None
    client, model = _make_llm_client()
    st.session_state["llm_client"] = client
    st.session_state["llm_model"] = model
    return client, model


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role": "system", "content": flow_instructions()},
        ]
    if "pending_ask" not in st.session_state:
        st.session_state["pending_ask"] = None


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


def _build_answer_json(pending: dict, answer_value: str | list[str]) -> str:
    """Build JSON string for tool result: { answer } or { selected }."""
    if pending.get("allow_multiple"):
        selected = answer_value if isinstance(answer_value, list) else [answer_value] if answer_value else []
        return json.dumps({"selected": selected})
    return json.dumps({"answer": answer_value if isinstance(answer_value, str) else str(answer_value or "")})


def _render_ask_form(pending: dict) -> None:
    """Show form for ask_user: prompt + input/selectbox/multiselect. On submit, append tool result and run step."""
    st.markdown(f"**{pending['prompt']}**")
    choices = pending.get("choices") or []
    allow_multiple = pending.get("allow_multiple", False)

    with st.form("ask_user_form", clear_on_submit=True):
        if choices:
            if allow_multiple:
                selected = st.multiselect("Select one or more", choices, key="ask_multiselect")
                submit_val = selected
            else:
                selected = st.selectbox("Choose one", [""] + choices, key="ask_selectbox")
                submit_val = selected if selected else None
        else:
            submit_val = st.text_input("Your answer", key="ask_text")

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
                st.error("Set OPENROUTER_API_KEY or OPENAI_API_KEY in .env or environment.")
                st.stop()
            # Run step in a loop until no more pending ask (or we get final reply)
            while True:
                messages, payload = run_agent_step(client, model, st.session_state["messages"])
                st.session_state["messages"] = messages
                if payload is not None:
                    st.session_state["pending_ask"] = payload
                    st.rerun()
                break
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Rental Search Assistant", page_icon="🏠")
    st.title("Rental Search Assistant")

    _ensure_env_loaded()
    _init_session_state()

    client, model = _get_client_and_model()
    if client is None or model is None:
        st.error("Set OPENROUTER_API_KEY (recommended) or OPENAI_API_KEY in .env or environment to run the assistant.")
        st.stop()

    _render_chat_history()

    pending = st.session_state.get("pending_ask")
    if pending is not None:
        with st.chat_message("assistant"):
            _render_ask_form(pending)
        return

    if prompt := st.chat_input("Type your search request (e.g. 2 bed in Vancouver under 3000)"):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        messages, payload = run_agent_step(client, model, st.session_state["messages"])
        st.session_state["messages"] = messages
        if payload is not None:
            st.session_state["pending_ask"] = payload
        st.rerun()


def run_ui() -> None:
    """Entry point for rental-search-ui script: start Streamlit server."""
    import sys
    import streamlit.web.cli as st_cli
    app_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(app_path), "--server.headless", "true"]
    st_cli.main()


if __name__ == "__main__":
    main()
