"""Unified API config: provider switch (API_PROVIDER), keys (OPENROUTER_API_KEY, OPENAI_API_KEY), and model vars (LLM_MODEL, EMBEDDING_MODEL)."""

import os
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_OPENROUTER = "openai/gpt-4o-mini"
DEFAULT_LLM_OPENAI = "gpt-4o-mini"
DEFAULT_EMBEDDING_OPENROUTER = "openai/text-embedding-3-small"
DEFAULT_EMBEDDING_OPENAI = "text-embedding-3-small"


def get_api_provider() -> Literal["openrouter", "openai"]:
    """Resolve which API provider to use. Reads API_PROVIDER or infers from which key is set (prefer openrouter)."""
    raw = (os.environ.get("API_PROVIDER") or "").strip().lower()
    if raw in ("openrouter", "openai"):
        return raw
    openrouter_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if openrouter_key:
        return "openrouter"
    if openai_key:
        return "openai"
    raise ValueError(
        "Set API_PROVIDER (openrouter or openai) and the corresponding API key "
        "(OPENROUTER_API_KEY or OPENAI_API_KEY) in .env."
    )


def get_api_key(provider: str | None = None) -> str:
    """Return the API key for the given provider (or resolved provider). Raises ValueError if missing."""
    p = provider or get_api_provider()
    if p == "openrouter":
        key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise ValueError("OPENROUTER_API_KEY is required when API_PROVIDER=openrouter. Set it in .env.")
        return key
    if p == "openai":
        key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not key:
            raise ValueError("OPENAI_API_KEY is required when API_PROVIDER=openai. Set it in .env.")
        return key
    raise ValueError(f"Unknown API_PROVIDER: {p}. Use openrouter or openai.")


def get_llm_model(provider: str | None = None) -> str:
    """Return the LLM model name for chat. Uses LLM_MODEL if set; else legacy OPENROUTER_MODEL/OPENAI_MODEL or default."""
    p = provider or get_api_provider()
    unified = (os.environ.get("LLM_MODEL") or "").strip()
    if unified:
        return unified
    if p == "openrouter":
        return (os.environ.get("OPENROUTER_MODEL") or "").strip() or DEFAULT_LLM_OPENROUTER
    return (os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_LLM_OPENAI


def get_embedding_model(provider: str | None = None) -> str:
    """Return the embedding model name. Uses EMBEDDING_MODEL if set; else legacy OPENAI_EMBEDDING_MODEL or default."""
    p = provider or get_api_provider()
    unified = (os.environ.get("EMBEDDING_MODEL") or "").strip()
    if unified:
        return unified
    legacy = (os.environ.get("OPENAI_EMBEDDING_MODEL") or "").strip()
    if legacy:
        return legacy
    return DEFAULT_EMBEDDING_OPENROUTER if p == "openrouter" else DEFAULT_EMBEDDING_OPENAI


def get_llm_client_and_model() -> tuple["OpenAI", str]:  # noqa: F821
    """Build OpenAI-compatible LLM client and model name from config. Raises ValueError if credentials missing."""
    from openai import OpenAI

    provider = get_api_provider()
    api_key = get_api_key(provider)
    model = get_llm_model(provider)
    if provider == "openrouter":
        client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/smakamali/rental_search_agent",
                "X-Title": "Rental Search Assistant",
            },
        )
    else:
        client = OpenAI(api_key=api_key)
    return client, model


def get_embedding_client_and_model() -> tuple["OpenAI", str]:  # noqa: F821
    """Build OpenAI-compatible client and embedding model name from config. Raises ValueError if credentials missing."""
    from openai import OpenAI

    provider = get_api_provider()
    api_key = get_api_key(provider)
    model = get_embedding_model(provider)
    if provider == "openrouter":
        client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    else:
        client = OpenAI(api_key=api_key)
    return client, model


def has_api_credentials() -> bool:
    """Return True if the resolved provider has a non-empty API key set."""
    try:
        get_api_key()
        return True
    except ValueError:
        return False
