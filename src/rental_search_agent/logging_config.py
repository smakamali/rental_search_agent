"""Shared logging configuration for rental_search_agent.

Call ``configure_logging()`` once at process start (CLI, Streamlit, MCP server).
Use ``log_stage`` for timed stage boundaries and ``new_run_id`` / ``set_run_id``
for optional per-search correlation (Phases 1–3).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Optional, Union

_PACKAGE_LOGGER_NAME = "rental_search_agent"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARKER = "_rental_search_agent_handler"
_CONFIGURED = False

_run_id: ContextVar[Optional[str]] = ContextVar("rental_search_agent_run_id", default=None)

# Word-ish tokens / patterns whose values must never appear in stage context logs.
_SECRET_KEY_TOKENS = frozenset(
    {"key", "api_key", "token", "password", "secret", "authorization", "credential"}
)


def new_run_id() -> str:
    """Return a new short correlation id for a search turn / request."""
    return uuid.uuid4().hex[:12]


def set_run_id(run_id: Optional[str]) -> None:
    """Set the current run_id (contextvar). Pass None to clear."""
    _run_id.set(run_id)


def get_run_id() -> Optional[str]:
    """Return the current run_id, or None if unset."""
    return _run_id.get()


def clear_run_id() -> None:
    """Clear the current run_id."""
    _run_id.set(None)


def _parse_level(level: Union[str, int, None]) -> int:
    """Resolve a logging level from an int, name, or LOG_LEVEL env (default INFO)."""
    if isinstance(level, int):
        return level
    if level is None:
        raw = os.environ.get("LOG_LEVEL", "INFO")
    else:
        raw = str(level)
    raw = (raw or "INFO").strip()
    if not raw:
        raw = "INFO"
    try:
        return int(raw)
    except ValueError:
        pass
    name = raw.upper()
    resolved = getattr(logging, name, None)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _resolve_log_file(
    log_file: Union[str, Path, None],
    project_root: Optional[Path],
) -> Optional[Path]:
    """Return a Path for file logging, or None if file logging is disabled.

    Uses the ``log_file`` argument if given; otherwise ``LOG_FILE`` from the
    environment. Relative paths are resolved against ``project_root`` (or cwd).
    """
    if log_file is None:
        env_val = os.environ.get("LOG_FILE", "").strip()
        if not env_val:
            return None
        log_file = env_val
    path = Path(log_file)
    if not path.is_absolute():
        root = project_root if project_root is not None else Path.cwd()
        path = root / path
    return path


def _remove_our_handlers(pkg: logging.Logger) -> None:
    for handler in list(pkg.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            pkg.removeHandler(handler)
            handler.close()


def configure_logging(
    *,
    level: Union[str, int, None] = None,
    log_file: Union[str, Path, None] = None,
    project_root: Optional[Path] = None,
    force: bool = False,
) -> None:
    """Configure the ``rental_search_agent`` package logger (and children).

    Idempotent: subsequent calls are no-ops unless ``force=True``.

    - Level from ``level`` or env ``LOG_LEVEL`` (default INFO).
    - Always attaches a console (stderr) StreamHandler.
    - Attaches a FileHandler only when ``log_file`` is passed or ``LOG_FILE`` is set.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    resolved_level = _parse_level(level)
    file_path = _resolve_log_file(log_file, project_root)

    pkg = logging.getLogger(_PACKAGE_LOGGER_NAME)
    pkg.setLevel(resolved_level)
    # Own handlers only — avoid duplicate lines via the root logger.
    pkg.propagate = False

    if force or _CONFIGURED:
        _remove_our_handlers(pkg)

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(resolved_level)
    console.setFormatter(formatter)
    setattr(console, _HANDLER_MARKER, True)
    pkg.addHandler(console)

    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, mode="a", encoding="utf-8")
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARKER, True)
        pkg.addHandler(file_handler)

    _CONFIGURED = True


def reset_logging_config() -> None:
    """Remove package handlers and clear the configured flag (for tests).

    Restores ``propagate=True`` and level ``NOTSET`` so later tests using
    ``caplog`` (root capture) are not poisoned by a prior ``configure_logging``.
    """
    global _CONFIGURED
    pkg = logging.getLogger(_PACKAGE_LOGGER_NAME)
    _remove_our_handlers(pkg)
    pkg.propagate = True
    pkg.setLevel(logging.NOTSET)
    _CONFIGURED = False


def _is_secret_key(key: str) -> bool:
    """True when ``key`` looks like a secret field name (never log its value)."""
    lower = key.lower()
    if lower in _SECRET_KEY_TOKENS:
        return True
    if lower.endswith("_key") or lower.endswith("_token") or lower.endswith("_secret"):
        return True
    # Underscore-separated tokens (e.g. access_token, client_secret).
    parts = lower.replace("-", "_").split("_")
    return any(part in _SECRET_KEY_TOKENS for part in parts)


def format_run_id_suffix() -> str:
    """Return `` run_id=...`` when a run id is set, else ``\"\"``."""
    rid = get_run_id()
    return f" run_id={rid}" if rid else ""


def _format_ctx(ctx: dict[str, Any]) -> str:
    if not ctx:
        return ""
    parts = [f"{k}={v}" for k, v in ctx.items() if not _is_secret_key(k)]
    if not parts:
        return ""
    return " " + " ".join(parts)


@contextmanager
def log_stage(
    logger: logging.Logger,
    name: str,
    level: int = logging.INFO,
    **ctx: Any,
) -> Iterator[None]:
    """Context manager that logs stage start/end with elapsed milliseconds.

    Optional ``ctx`` fields are appended as ``key=value`` (secret-looking keys
    are omitted). If a ``run_id`` is set via ``set_run_id``, it is included
    unless already present in ``ctx``. On exception, logs at WARNING with
    ``exc_info=True`` (start/end still use ``level``).
    """
    extras = dict(ctx)
    rid = get_run_id()
    if rid is not None:
        extras.setdefault("run_id", rid)
    ctx_suffix = _format_ctx(extras)
    logger.log(level, "stage start name=%s%s", name, ctx_suffix)
    started = time.perf_counter()
    try:
        yield
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "stage error name=%s elapsed_ms=%.1f%s",
            name,
            elapsed_ms,
            ctx_suffix,
            exc_info=True,
        )
        raise
    else:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.log(
            level,
            "stage end name=%s elapsed_ms=%.1f%s",
            name,
            elapsed_ms,
            ctx_suffix,
        )
