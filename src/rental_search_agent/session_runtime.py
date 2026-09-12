"""Per-request UI runtime: principal, guest quotas, and active preference I/O.

Streamlit sets this context at the start of each script run / agent step so
``client.run_tool`` and preference fill-in respect guest vs authenticated rules
without importing Streamlit into the domain layer.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping, MutableMapping

from rental_search_agent.auth_principal import Principal
from rental_search_agent.capability_policy import CapabilityPolicy
from rental_search_agent.preference_store import (
    LOCAL_USER_ID,
    FilePreferenceStore,
    get_preference_store_for,
    get_sqlite_preference_store,
    normalize_preferences,
    prefs_are_empty,
)

_principal: ContextVar[Principal | None] = ContextVar("rsa_principal", default=None)
_searches_used: ContextVar[int] = ContextVar("rsa_searches_used", default=0)
_has_results: ContextVar[bool] = ContextVar("rsa_has_results", default=False)
# Mutable prefs dict shared with Streamlit session_state for guests / working copy.
_prefs_holder: ContextVar[MutableMapping[str, str] | None] = ContextVar(
    "rsa_prefs_holder", default=None
)


def set_runtime(
    principal: Principal,
    *,
    searches_used: int = 0,
    has_results: bool = False,
    prefs_holder: MutableMapping[str, str] | None = None,
) -> None:
    _principal.set(principal)
    _searches_used.set(max(0, int(searches_used)))
    _has_results.set(bool(has_results))
    _prefs_holder.set(prefs_holder)


def clear_runtime() -> None:
    _principal.set(None)
    _searches_used.set(0)
    _has_results.set(False)
    _prefs_holder.set(None)


def get_runtime_principal() -> Principal:
    p = _principal.get()
    if p is not None:
        return p
    # CLI / MCP / unset: unrestricted local operator.
    return Principal(kind="dev", user_id=LOCAL_USER_ID, name="Local")


def get_searches_used() -> int:
    return max(0, int(_searches_used.get() or 0))


def set_searches_used(n: int) -> None:
    _searches_used.set(max(0, int(n)))


def get_has_results() -> bool:
    return bool(_has_results.get())


def set_has_results(value: bool) -> None:
    _has_results.set(bool(value))


def get_capability_policy() -> CapabilityPolicy:
    return CapabilityPolicy(
        principal=get_runtime_principal(),
        searches_used=get_searches_used(),
        has_results=get_has_results(),
    )


def load_active_preferences() -> dict[str, str]:
    """Load prefs for the current principal (holder → durable store → file)."""
    holder = _prefs_holder.get()
    if holder is not None:
        return normalize_preferences(holder)

    principal = get_runtime_principal()
    store = get_preference_store_for(principal)
    if store is not None and principal.user_id:
        return store.load(principal.user_id)
    return FilePreferenceStore().load(LOCAL_USER_ID)


def save_active_preferences(prefs: Mapping[str, Any]) -> None:
    """Persist prefs for the current principal.

    Guests: update prefs_holder only (no durable write).
    Authenticated: SQLite. Dev / unset: local JSON file.
    """
    normalized = normalize_preferences(prefs)
    holder = _prefs_holder.get()
    if holder is not None:
        holder.clear()
        holder.update(normalized)

    principal = get_runtime_principal()
    if principal.is_guest:
        return

    store = get_preference_store_for(principal)
    if store is not None and principal.user_id:
        store.save(principal.user_id, normalized)
        return
    FilePreferenceStore().save(LOCAL_USER_ID, normalized)


def merge_guest_prefs_on_login(
    principal: Principal,
    guest_prefs: Mapping[str, Any],
) -> dict[str, str]:
    """Upsert user; copy guest prefs into SQLite only if stored prefs are empty."""
    if not principal.is_authenticated or not principal.user_id:
        return normalize_preferences(guest_prefs)

    store = get_sqlite_preference_store()
    store.upsert_user(
        principal.user_id,
        email=principal.email,
        name=principal.name,
        picture_url=principal.picture_url,
    )
    stored = store.load(principal.user_id)
    guest_norm = normalize_preferences(guest_prefs)
    if prefs_are_empty(stored) and not prefs_are_empty(guest_norm):
        store.save(principal.user_id, guest_norm)
        return guest_norm
    if not prefs_are_empty(stored):
        return stored
    return guest_norm
