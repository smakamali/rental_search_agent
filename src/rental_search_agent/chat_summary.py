"""Summarize conversation for use as context in listing analysis (e.g. user's stated criteria)."""

import logging
from typing import Any

from rental_search_agent.api_config import get_llm_client_and_model

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = (
    "Summarize in 2-3 sentences the user's stated rental criteria and preferences from this conversation. "
    "Include only what the user said or agreed to. No speculation. Be concise."
)


def summarize_conversation_for_preferences(messages: list[dict[str, Any]]) -> str:
    """Build a short summary of the conversation for use as preference context.

    Uses only user and assistant messages; skips system and tool messages.
    Calls the LLM once to produce a 2-3 sentence summary. Returns empty string
    if there are no user/assistant messages or on LLM failure (so the UI can
    still run analysis without context).
    """
    parts: list[str] = []
    for msg in messages:
        role = (msg.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        parts.append(f"{label}: {content}")
    if not parts:
        return ""

    conversation = "\n\n".join(parts)
    try:
        client, model = get_llm_client_and_model()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": conversation},
            ],
            temperature=0,
        )
        content = (response.choices[0].message.content or "").strip()
        return content
    except Exception as e:
        logger.warning("Chat summary failed: %s", e)
        return ""
