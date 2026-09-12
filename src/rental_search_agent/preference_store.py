"""Preference persistence: file (local/CLI) and SQLite (authenticated users)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from rental_search_agent.preference_resolution import PREF_KEYS

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "local"

_DEFAULT_PREFS_DIR = Path.home() / ".rental_search_agent"


def default_preferences_file_path() -> Path:
    return _DEFAULT_PREFS_DIR / "preferences.json"


def default_preferences_db_path() -> Path:
    raw = (os.environ.get("PREFS_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PREFS_DIR / "preferences.db"


def empty_preferences() -> dict[str, str]:
    return {k: "" for k in PREF_KEYS}


def normalize_preferences(prefs: Mapping[str, object] | None) -> dict[str, str]:
    """Return a full PREF_KEYS dict with string values."""
    src = prefs or {}
    return {k: str(src.get(k, "") or "") for k in PREF_KEYS}


def prefs_are_empty(prefs: Mapping[str, object] | None) -> bool:
    """True when every preference value is blank (or prefs missing)."""
    normalized = normalize_preferences(prefs)
    return all(not (v or "").strip() for v in normalized.values())


@runtime_checkable
class PreferenceStore(Protocol):
    def load(self, user_id: str) -> dict[str, str]:
        """Load preferences for user_id; empty strings for missing keys."""

    def save(self, user_id: str, prefs: Mapping[str, object]) -> None:
        """Persist preferences for user_id (normalized to PREF_KEYS)."""


class FilePreferenceStore:
    """Single shared JSON file; user_id is ignored (local/CLI / dev)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_preferences_file_path()

    def load(self, user_id: str = LOCAL_USER_ID) -> dict[str, str]:
        _ = user_id
        default = empty_preferences()
        if not self.path.exists():
            return default
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return default
            return normalize_preferences(data)
        except Exception:
            logger.warning(
                "Failed to load preferences from %s; using defaults",
                self.path,
                exc_info=True,
            )
            return default

    def save(self, user_id: str, prefs: Mapping[str, object]) -> None:
        _ = user_id
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = normalize_preferences(prefs)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save preferences to %s", self.path, exc_info=True)


class SqlitePreferenceStore:
    """Per-user preferences in SQLite (authenticated Streamlit users)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_preferences_db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id TEXT PRIMARY KEY,
                        email TEXT,
                        name TEXT,
                        picture_url TEXT,
                        created_at TEXT NOT NULL,
                        last_login_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS preferences (
                        user_id TEXT PRIMARY KEY,
                        prefs_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES users(user_id)
                    );
                    """
                )
                conn.commit()
        except Exception:
            logger.warning(
                "Failed to ensure preferences DB schema path=%s",
                self.path,
                exc_info=True,
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_user(
        self,
        user_id: str,
        *,
        email: str = "",
        name: str = "",
        picture_url: str = "",
    ) -> None:
        now = self._now()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT user_id FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO users (
                            user_id, email, name, picture_url, created_at, last_login_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (user_id, email, name, picture_url, now, now),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE users
                        SET email = ?, name = ?, picture_url = ?, last_login_at = ?
                        WHERE user_id = ?
                        """,
                        (email, name, picture_url, now, user_id),
                    )
                conn.commit()
        except Exception:
            logger.warning("upsert_user failed user_id=%s", user_id, exc_info=True)

    def load(self, user_id: str) -> dict[str, str]:
        default = empty_preferences()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT prefs_json FROM preferences WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            if row is None:
                return default
            data = json.loads(row["prefs_json"])
            if not isinstance(data, dict):
                return default
            return normalize_preferences(data)
        except Exception:
            logger.warning(
                "Failed to load preferences from DB user_id=%s path=%s",
                user_id,
                self.path,
                exc_info=True,
            )
            return default

    def save(self, user_id: str, prefs: Mapping[str, object]) -> None:
        payload = normalize_preferences(prefs)
        now = self._now()
        try:
            with self._connect() as conn:
                # Ensure a users row exists so FK is satisfied when enabled.
                existing = conn.execute(
                    "SELECT user_id FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO users (
                            user_id, email, name, picture_url, created_at, last_login_at
                        ) VALUES (?, '', '', '', ?, ?)
                        """,
                        (user_id, now, now),
                    )
                conn.execute(
                    """
                    INSERT INTO preferences (user_id, prefs_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        prefs_json = excluded.prefs_json,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, json.dumps(payload), now),
                )
                conn.commit()
        except Exception:
            logger.warning(
                "Failed to save preferences to DB user_id=%s path=%s",
                user_id,
                self.path,
                exc_info=True,
            )


_file_store: FilePreferenceStore | None = None
_sqlite_store: SqlitePreferenceStore | None = None


def get_preference_store() -> PreferenceStore:
    """Default store for CLI/MCP/local (shared JSON file)."""
    global _file_store
    if _file_store is None:
        _file_store = FilePreferenceStore()
    return _file_store


def get_sqlite_preference_store() -> SqlitePreferenceStore:
    global _sqlite_store
    if _sqlite_store is None:
        _sqlite_store = SqlitePreferenceStore()
    return _sqlite_store


def reset_preference_store_cache() -> None:
    """Test helper: clear cached store singletons."""
    global _file_store, _sqlite_store
    _file_store = None
    _sqlite_store = None


def get_preference_store_for(principal: object) -> PreferenceStore | None:
    """Return durable store for principal, or None for guests (session-only).

    ``principal`` is duck-typed (``kind`` attribute) to avoid import cycles.
    """
    kind = getattr(principal, "kind", None)
    if kind == "authenticated":
        return get_sqlite_preference_store()
    if kind == "dev":
        return get_preference_store()
    # guest or unknown: ephemeral only
    return None
