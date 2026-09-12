"""Resolve the current Streamlit (or local) auth principal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from rental_search_agent.auth_allowlist import email_on_allowlist

PrincipalKind = Literal["guest", "authenticated", "dev"]


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    user_id: str = ""
    email: str = ""
    name: str = ""
    picture_url: str = ""
    allowlist_denied: bool = False

    @property
    def is_authenticated(self) -> bool:
        return self.kind == "authenticated"

    @property
    def is_guest(self) -> bool:
        return self.kind == "guest"

    @property
    def is_dev(self) -> bool:
        return self.kind == "dev"

    @property
    def has_full_access(self) -> bool:
        """Authenticated or local-dev (no OIDC) — no guest caps."""
        return self.kind in ("authenticated", "dev")


def _auth_configured() -> bool:
    """True when Streamlit OIDC secrets appear to be configured."""
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is None:
            return False
        try:
            auth = secrets.get("auth", None)  # type: ignore[attr-defined]
        except Exception:
            try:
                auth = secrets["auth"]
            except Exception:
                return False
        if not auth:
            return False

        def _get(mapping: Any, key: str) -> Any:
            try:
                if hasattr(mapping, "get"):
                    return mapping.get(key)
                return mapping[key]
            except Exception:
                return None

        # Flat [auth] client_id, or nested [auth.google] / first provider section.
        if _get(auth, "client_id"):
            return True
        for provider in ("google", "microsoft", "auth0"):
            section = _get(auth, provider)
            if section and _get(section, "client_id"):
                return True
        return False
    except Exception:
        return False


def _read_st_user() -> Any | None:
    try:
        import streamlit as st

        return getattr(st, "user", None)
    except Exception:
        return None


def current_principal(*, auth_configured: bool | None = None) -> Principal:
    """Return guest / authenticated / dev principal for the UI.

    - OIDC not configured → ``dev`` (local unrestricted FileStore path).
    - Not logged in → ``guest``.
    - Logged in + allowlist pass (or allowlist off) → ``authenticated``.
    - Logged in + allowlist fail → ``guest`` with ``allowlist_denied=True``.
    """
    configured = _auth_configured() if auth_configured is None else auth_configured
    if not configured:
        return Principal(kind="dev", user_id="local", name="Local dev")

    user = _read_st_user()
    is_logged_in = bool(getattr(user, "is_logged_in", False)) if user is not None else False
    if not is_logged_in:
        return Principal(kind="guest")

    email = str(getattr(user, "email", "") or "")
    name = str(getattr(user, "name", "") or "")
    picture = str(getattr(user, "picture", "") or "")
    sub = str(getattr(user, "sub", "") or "") or email or "unknown"

    if not email_on_allowlist(email):
        return Principal(
            kind="guest",
            user_id="",
            email=email,
            name=name,
            picture_url=picture,
            allowlist_denied=True,
        )

    return Principal(
        kind="authenticated",
        user_id=sub,
        email=email,
        name=name,
        picture_url=picture,
    )
