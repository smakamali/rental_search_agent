"""Shared display formatting for listing analysis and related UI."""

from __future__ import annotations

import math
import re
from typing import Any, Optional
from urllib.parse import urlparse

# Piecewise RGB stops: orange → amber → yellow-green → green (no failure-red).
_SCORE_COLOR_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (230, 126, 34)),
    (60.0, (243, 156, 18)),
    (75.0, (168, 184, 48)),
    (90.0, (39, 174, 96)),
    (100.0, (22, 160, 133)),
)


def _is_finite_number(value: Any) -> bool:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(n)


def format_currency(value: Any, *, unavailable: str = "—") -> str:
    """Format a CAD amount as $1,000,000 (whole dollars). Never uses scientific notation."""
    if value is None or value == "":
        return unavailable
    if not _is_finite_number(value):
        return unavailable
    return f"${int(round(float(value))):,}"


def parse_budget_input(raw: Any) -> Optional[str]:
    """Parse user budget text into a canonical numeric string for preference storage.

    Accepts values such as ``1000000``, ``1000000.0``, ``$1,000,000``, ``1,000,000``.
    Returns ``None`` for empty/invalid input (callers keep the field empty rather than crashing).
    Whole-dollar values are stored without a trailing ``.0``.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if not math.isfinite(float(raw)):
            return None
        n = float(raw)
        return str(int(round(n))) if n == int(n) else f"{n:g}"
    s = str(raw).strip()
    if not s:
        return None
    cleaned = s.replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        n = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(n) or n < 0:
        return None
    if n == int(n):
        return str(int(n))
    return f"{n:g}"


def format_budget_input(raw: Any, *, empty: str = "") -> str:
    """Format a stored budget preference for a text input (``$1,000,000``)."""
    if raw is None or str(raw).strip() == "":
        return empty
    parsed = parse_budget_input(raw)
    if parsed is None:
        # Preserve unparseable user text so they can fix it without silent wipe.
        return str(raw).strip()
    try:
        return format_currency(float(parsed), unavailable=empty)
    except (TypeError, ValueError):
        return str(raw).strip()


def format_duration(minutes: Any, *, unavailable: str = "—") -> str:
    """Format a travel time as '2 min'."""
    if minutes is None or minutes == "":
        return unavailable
    if not _is_finite_number(minutes):
        return unavailable
    n = float(minutes)
    rounded = int(round(n))
    return f"{rounded} min"


def format_sqft(value: Any, *, unavailable: str = "—") -> str:
    """Format area as '812 sq ft'."""
    if value is None or value == "":
        return unavailable
    if not _is_finite_number(value):
        return unavailable
    n = float(value)
    if n == int(n):
        return f"{int(n):,} sq ft"
    return f"{n:g} sq ft"


def format_count(value: Any, *, unavailable: str = "—") -> str:
    """Format a bed/bath-style count without trailing .0."""
    if value is None or value == "":
        return unavailable
    if not _is_finite_number(value):
        return unavailable
    n = float(value)
    if n == int(n):
        return str(int(n))
    return f"{n:g}"


def format_percentage(value: Any, *, from_fraction: bool = False, unavailable: str = "—") -> str:
    """Format as '90%'. Pass from_fraction=True for 0–1 scores."""
    if value is None or value == "":
        return unavailable
    if not _is_finite_number(value):
        return unavailable
    n = float(value)
    if from_fraction:
        n = n * 100.0
    return f"{int(round(n))}%"


def score_to_pct(value: Any) -> Optional[int]:
    """Convert a 0–1 or already-percent score to an int 0–100, or None if missing."""
    if value is None or value == "":
        return None
    if not _is_finite_number(value):
        return None
    n = float(value)
    if 0.0 <= n <= 1.0:
        return int(round(n * 100.0))
    if 1.0 < n <= 100.0:
        return int(round(n))
    return int(round(max(0.0, min(100.0, n))))


def get_score_color(score: Any) -> str:
    """Return an #rrggbb color for a 0–100 score (or 0–1 fraction). Centralized mapping."""
    pct = score_to_pct(score)
    if pct is None:
        return "#888888"
    x = float(max(0, min(100, pct)))
    for i in range(1, len(_SCORE_COLOR_STOPS)):
        x1, c1 = _SCORE_COLOR_STOPS[i - 1]
        x2, c2 = _SCORE_COLOR_STOPS[i]
        if x <= x2 or i == len(_SCORE_COLOR_STOPS) - 1:
            if x2 == x1:
                t = 1.0
            else:
                t = (x - x1) / (x2 - x1)
            t = max(0.0, min(1.0, t))
            r = int(round(c1[0] + t * (c2[0] - c1[0])))
            g = int(round(c1[1] + t * (c2[1] - c1[1])))
            b = int(round(c1[2] + t * (c2[2] - c1[2])))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#168a75"


def format_criterion_comparison(
    observed: Optional[str],
    comparator: Optional[str],
    required: Optional[str],
    *,
    unknown_text: str = "Not mentioned",
) -> str:
    """Join observed, comparator, and required, e.g. '2 min ≤ 5 min'."""
    obs = (observed or "").strip()
    req = (required or "").strip()
    cmp_ = (comparator or "").strip()
    if not obs and not req:
        return unknown_text
    if obs and req and cmp_:
        return f"{obs} {cmp_} {req}"
    if obs and req:
        # Range requirements (e.g. beds 2–3) previously rendered as "2 (2–3)".
        if "–" in req or "-" in req:
            return f"{obs} in required {req}"
        return f"{obs} ({req})"
    if obs:
        return obs
    return req


# Display-only proximity chip patterns (no LLM / network). Scoring still uses proximity_parser.
_PROXIMITY_CHIP_RE = re.compile(
    r"(?i)(?:(?:max|within|under|<=|≤)\s*)?(\d+)\s*(?:min(?:ute)?s?)\s+"
    r"(?:(?:walk|drive|transit|by\s+car|driving|walking)\s+)?(?:to\s+)?(.+)",
)


def proximity_chips_from_text(text: str) -> list[str]:
    """Extract compact display chips from proximity preference text without network calls.

    This is display-only and does not replace ``parse_proximity_preferences`` for scoring.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    chips: list[str] = []
    seen: set[str] = set()
    for line in re.split(r"[\n;]+", raw):
        line = line.strip().strip("-•*").strip()
        if not line:
            continue
        m = _PROXIMITY_CHIP_RE.search(line)
        if not m:
            continue
        minutes, dest = m.group(1), m.group(2).strip().rstrip(".,;")
        dest = re.sub(r"\s+", " ", dest)
        if not dest:
            continue
        # Normalize common transit phrasing for compact chips.
        dest_l = dest.lower()
        if "transit" in dest_l or "skytrain" in dest_l or "station" in dest_l:
            if "nearest" in dest_l or dest_l in ("transit", "transit station", "a transit station"):
                dest = "transit"
        label = f"≤ {minutes} min to {dest}"
        key = label.lower()
        if key not in seen:
            seen.add(key)
            chips.append(label)
    return chips


def proximity_chips_from_rules(rules: Any) -> list[str]:
    """Format already-parsed proximity rules as display chips (no network)."""
    if not rules:
        return []
    chips: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        if hasattr(rule, "model_dump"):
            rule = rule.model_dump()
        if not isinstance(rule, dict):
            continue
        loc = str(rule.get("location") or "").strip()
        minutes = rule.get("max_minutes")
        if not loc or minutes is None:
            continue
        try:
            mins = int(round(float(minutes)))
        except (TypeError, ValueError):
            continue
        loc_l = loc.lower()
        if loc_l == "nearest transit station" or "transit" in loc_l:
            dest = "transit"
        else:
            dest = loc
        label = f"≤ {mins} min to {dest}"
        key = label.lower()
        if key not in seen:
            seen.add(key)
            chips.append(label)
    return chips


def listing_preference_chips(text: str) -> list[str]:
    """Display-only chips from comma/newline qualitative preferences (not a scorer)."""
    raw = (text or "").strip()
    if not raw:
        return []
    chips: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[\n,;]+", raw):
        label = re.sub(r"\s+", " ", part).strip().strip("-•*")
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        chips.append(label)
    return chips


def criterion_source_label(source: Optional[str]) -> str:
    """Canonical visible source badge text. Does not invent AI provenance."""
    raw = (source or "").strip()
    if not raw:
        return "—"
    key = raw.lower()
    if key == "mls":
        return "MLS"
    if key == "calculated":
        return "Calculated"
    if key == "inferred":
        # Deterministic description/amenity matching — not LLM. Keep accurate label.
        return "Inferred"
    if key in ("ai inferred", "ai_inferred", "llm"):
        return "AI Inferred"
    return raw


def criterion_source_help(source: Optional[str]) -> Optional[str]:
    """Optional tooltip for source badges."""
    key = (source or "").strip().lower()
    if key == "inferred":
        return (
            "Matched from the property description or amenity text rather than a "
            "structured MLS field."
        )
    if key in ("ai inferred", "ai_inferred", "llm"):
        return (
            "Evaluated from the property description using AI rather than a "
            "structured MLS field."
        )
    if key == "calculated":
        return "Derived from travel-time / proximity calculation."
    if key == "mls":
        return "Taken from structured listing (MLS) fields."
    return None


def split_listing_address(
    address: str | None,
    postal_code: str | None = None,
) -> tuple[str, str]:
    """Split a listing address into (headline, locality line).

    Realtor.ca addresses are typically 'street, City, Province Postal'. When the
    split is unclear, the full address is the headline and locality is empty.
    """
    raw = (address or "").strip()
    postal = (postal_code or "").strip()
    if not raw:
        return ("Listing", postal)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2:
        headline = parts[0]
        locality = ", ".join(parts[1:])
    else:
        headline = raw
        locality = ""
    if postal:
        compact_local = re.sub(r"\s+", "", locality).lower()
        compact_postal = re.sub(r"\s+", "", postal).lower()
        if compact_postal and compact_postal not in compact_local:
            locality = f"{locality} {postal}".strip() if locality else postal
    return headline, locality


def safe_http_url(url: str | None) -> str | None:
    """Return url if it is http(s); else None (blocks javascript: and other schemes)."""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return raw


def proximity_criterion_name(mode: str | None, location: str | None) -> str:
    """Human name for a proximity rule, e.g. 'Walk to nearest transit station'."""
    loc = (location or "location").strip() or "location"
    m = (mode or "travel").strip().lower()
    verb = {"walk": "Walk", "drive": "Drive", "transit": "Transit"}.get(m, m.title() or "Travel")
    return f"{verb} to {loc}"


def escape_markdown_link_text(text: str) -> str:
    """Escape characters that would let untrusted text break out of a markdown link label."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
