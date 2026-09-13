"""Package-relative UI asset helpers for Analyze panel icons."""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

ANALYSIS_ICON_DIR = Path(__file__).resolve().parent / "assets" / "analysis_icons"

# Canonical icon keys -> filenames under ANALYSIS_ICON_DIR.
ANALYSIS_ICONS: Mapping[str, str] = {
    "met": "status_met.png",
    "unmet": "status_unmet.png",
    "partial": "status_partial.png",
    "unknown": "status_unknown.png",
    "commute": "highlight_commute.png",
    "space": "highlight_space.png",
    "budget": "highlight_budget.png",
    "parking": "highlight_parking.png",
    "generic": "highlight_generic.png",
}

STATUS_ICON_KEYS = frozenset({"met", "unmet", "partial", "unknown"})
HIGHLIGHT_ICON_KEYS = frozenset({"commute", "space", "budget", "parking", "generic"})

# Text fallbacks when a PNG cannot be loaded (accessibility still comes from aria-label).
STATUS_TEXT_FALLBACK = {
    "met": "✓",
    "unmet": "✕",
    "partial": "~",
    "unknown": "?",
}

# Map legacy / alternate highlight keys onto canonical asset keys.
_HIGHLIGHT_KEY_ALIASES = {
    "other": "generic",
}


def analysis_icon_path(name: str) -> Path | None:
    """Return the package path for a known icon name, or None if unknown/unsafe."""
    key = (name or "").strip().lower()
    key = _HIGHLIGHT_KEY_ALIASES.get(key, key)
    filename = ANALYSIS_ICONS.get(key)
    if not filename:
        return None
    path = (ANALYSIS_ICON_DIR / filename).resolve()
    try:
        path.relative_to(ANALYSIS_ICON_DIR.resolve())
    except ValueError:
        logger.warning("Rejected icon path outside analysis icon dir: %s", path)
        return None
    return path


def normalize_highlight_icon_key(icon_key: str | None) -> str:
    """Map a highlight icon_key to a canonical asset key (defaults to generic)."""
    key = (icon_key or "").strip().lower()
    key = _HIGHLIGHT_KEY_ALIASES.get(key, key)
    if key in HIGHLIGHT_ICON_KEYS:
        return key
    return "generic"


def normalize_status_icon_key(status: str | None) -> str:
    """Map a criterion status to a canonical status icon key."""
    key = (status or "").strip().lower()
    if key in STATUS_ICON_KEYS:
        return key
    return "unknown"


@lru_cache(maxsize=None)
def icon_data_uri(name: str) -> str | None:
    """Return a ``data:image/png;base64,...`` URI for a known icon, or None.

    Only known keys from ``ANALYSIS_ICONS`` are accepted — arbitrary filesystem
    paths are rejected. Missing files return None (caller should use fallback).
    """
    path = analysis_icon_path(name)
    if path is None:
        return None
    if not path.is_file():
        logger.warning("Analysis icon asset missing: %s", path)
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read analysis icon %s: %s", path, exc)
        return None
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def status_icon_img_html(status: str, *, size_px: int = 20) -> str:
    """HTML for a criterion status icon (img or text fallback)."""
    key = normalize_status_icon_key(status)
    uri = icon_data_uri(key)
    if uri:
        return (
            f'<img class="rsa-status-icon" src="{uri}" width="{size_px}" height="{size_px}" '
            f'alt="" aria-hidden="true" />'
        )
    fallback = STATUS_TEXT_FALLBACK.get(key, "?")
    return f'<span class="rsa-status-fallback" aria-hidden="true">{fallback}</span>'


def highlight_icon_img_html(icon_key: str | None, *, size_px: int = 40) -> str:
    """HTML for a positive-highlight icon (img or empty fallback)."""
    key = normalize_highlight_icon_key(icon_key)
    uri = icon_data_uri(key)
    if uri:
        return (
            f'<img class="rsa-highlight-icon-img" src="{uri}" width="{size_px}" '
            f'height="{size_px}" alt="" aria-hidden="true" />'
        )
    return '<span class="rsa-highlight-icon-fallback" aria-hidden="true"></span>'
