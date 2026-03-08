"""Parse free-text proximity preferences into structured ProximityRule list."""

import re
from typing import List

from rental_search_agent.models import ProximityRule

# Phrases that mean "nearest transit station" (not geocoded as an address).
TRANSIT_LOCATION_PATTERNS = re.compile(
    r"\b(skytrain|train\s*station|transit\s*station|transit\s*stop|nearest\s*transit|transit)\b",
    re.IGNORECASE,
)
NEAREST_TRANSIT_LOCATION = "nearest transit station"

# Single-rule: "max 30 min drive to downtown" or "30 min walk to work"
# Captures: (number), (drive|walk|transit), (location)
SINGLE_RULE_PATTERN = re.compile(
    r"(?:max\s+)?(\d+(?:\.\d+)?)\s*min(?:ute)?s?\s+(drive|walk|transit)\s+to\s+(.+)",
    re.IGNORECASE,
)


def _normalize_location(raw: str) -> str:
    """Normalize location string; 'skytrain' / 'transit station' -> 'nearest transit station'."""
    raw = raw.strip()
    if not raw:
        return raw
    if TRANSIT_LOCATION_PATTERNS.search(raw):
        return NEAREST_TRANSIT_LOCATION
    return raw


def parse_proximity_preferences(proximity_text: str) -> List[ProximityRule]:
    """Parse free-text proximity preferences into a list of ProximityRule.

    Handles patterns like:
      - "max 30 min drive to downtown Vancouver"
      - "5 min walk to skytrain"
      - "20 minutes transit to Broadway"
    Multiple rules can be separated by commas or " and ".
    """
    if not (proximity_text or proximity_text.strip()):
        return []

    text = proximity_text.strip()
    rules: List[ProximityRule] = []
    seen: set[tuple[str, str, float]] = set()

    # Split by comma or " and " to get candidate segments
    segments = re.split(r"\s*,\s*|\s+and\s+", text, flags=re.IGNORECASE)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        m = SINGLE_RULE_PATTERN.search(segment)
        if m:
            num_str, mode, location = m.group(1), m.group(2).lower(), m.group(3).strip()
            try:
                max_min = float(num_str)
            except ValueError:
                continue
            location = _normalize_location(location)
            key = (location, mode, max_min)
            if key not in seen:
                seen.add(key)
                rules.append(ProximityRule(location=location, mode=mode, max_minutes=max_min))

    return rules
