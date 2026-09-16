"""Preferred facing/direction: parse, listing extraction, and circular scoring.

Scoring only — never used as a hard filter. Missing listing evidence omits the
component (None) rather than scoring silence as zero.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Union

CANONICAL_DIRECTIONS: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

DIRECTION_LABELS: dict[str, str] = {
    "N": "North",
    "NE": "North-East",
    "E": "East",
    "SE": "South-East",
    "S": "South",
    "SW": "South-West",
    "W": "West",
    "NW": "North-West",
}

DIRECTION_INDEX: dict[str, int] = {code: i for i, code in enumerate(CANONICAL_DIRECTIONS)}

# Adjacent cardinals (N/E/S/W) compose the intercardinal between them. A corner
# unit "facing east and south" is South-East, not two unrelated exposures.
_INTERCARDINAL_FROM_CARDINALS: dict[frozenset[str], str] = {
    frozenset({"N", "E"}): "NE",
    frozenset({"E", "S"}): "SE",
    frozenset({"S", "W"}): "SW",
    frozenset({"W", "N"}): "NW",
}

# Circular step distance → score. SW pref: S/W are 1 step; N/E are 3; NE is opposite.
STEP_SCORES: dict[int, float] = {
    0: 1.00,
    1: 0.70,
    2: 0.35,
    3: 0.10,
    4: 0.00,
}

# Longest aliases first so "south-west" wins over "south" / "west".
_TOKEN_ALIASES: dict[str, str] = {
    "north-east": "NE",
    "north east": "NE",
    "northeast": "NE",
    "northeasterly": "NE",
    "north-west": "NW",
    "north west": "NW",
    "northwest": "NW",
    "northwesterly": "NW",
    "south-east": "SE",
    "south east": "SE",
    "southeast": "SE",
    "southeasterly": "SE",
    "south-west": "SW",
    "south west": "SW",
    "southwest": "SW",
    "southwesterly": "SW",
    "northern": "N",
    "southern": "S",
    "eastern": "E",
    "western": "W",
    "north": "N",
    "south": "S",
    "east": "E",
    "west": "W",
    "ne": "NE",
    "nw": "NW",
    "se": "SE",
    "sw": "SW",
    "n": "N",
    "s": "S",
    "e": "E",
    "w": "W",
}

_PLACE_NAME_PHRASES: tuple[str, ...] = (
    "district of north vancouver",
    "city of north vancouver",
    "downtown eastside",
    "north vancouver",
    "west vancouver",
    "south of fraser",
    "south granville",
    "south surrey",
    "south delta",
    "south calgary",
    "south toronto",
    "north shore",
    "north york",
    "north van",
    "north delta",
    "north calgary",
    "north toronto",
    "east vancouver",
    "east york",
    "east side",
    "east van",
    "east calgary",
    "east toronto",
    "west kelowna",
    "west side",
    "west end",
    "west van",
    "west calgary",
    "west toronto",
)

_COMPASS_TOKEN = (
    r"(?:north-?east|north-?west|south-?east|south-?west|"
    r"northeast|northwest|southeast|southwest|"
    r"northeasterly|northwesterly|southeasterly|southwesterly|"
    r"northern|southern|eastern|western|"
    r"north|south|east|west|ne|nw|se|sw)"
)

_CONTEXT_TRAILING = re.compile(
    rf"((?:{_COMPASS_TOKEN})(?:\s*(?:and|&|/|,|or)\s*(?:{_COMPASS_TOKEN}))*)"
    rf"\s*-?\s*(?:facing|faced|faces|exposure|exposed|aspect|oriented)\b",
    re.IGNORECASE,
)
_CONTEXT_LEADING = re.compile(
    rf"\b(?:facing|faces|faced|exposure|exposed(?:\s+to)?|aspect|oriented(?:\s+to)?)"
    rf"\s+(?:the\s+)?((?:{_COMPASS_TOKEN})(?:\s*(?:and|&|/|,|or)\s*(?:{_COMPASS_TOKEN}))*)\b",
    re.IGNORECASE,
)
_HYPHEN_FACING = re.compile(
    rf"({_COMPASS_TOKEN})\s*-\s*facing\b",
    re.IGNORECASE,
)
_LETTER_CONTEXT = re.compile(
    r"(?:facing|faces|faced|exposure|exposed|aspect)\s*[:\-]?\s*\b([nsew])\b"
    r"|\b([nsew])\s*-\s*facing\b",
    re.IGNORECASE,
)
_ALIAS_SCAN = re.compile(
    r"\b(?:"
    r"north-?east|north-?west|south-?east|south-?west|"
    r"northeast|northwest|southeast|southwest|"
    r"northeasterly|northwesterly|southeasterly|southwesterly|"
    r"northern|southern|eastern|western|"
    r"north|south|east|west|ne|nw|se|sw"
    r")\b",
    re.IGNORECASE,
)

_FACING_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*(?:Facing|Direction|Directions)\s*:\s*(.+?)(?=\n\s*(?:Proximity|Facing|Direction|Directions)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _listing_attr(listing: Union[dict, Any], key: str) -> Any:
    if isinstance(listing, dict):
        return listing.get(key)
    return getattr(listing, key, None)


def listing_text_blob_for_facing(listing: Union[dict, Any]) -> str:
    parts = [
        _listing_attr(listing, "description") or "",
        _listing_attr(listing, "ammenities") or "",
        _listing_attr(listing, "nearby_ammenities") or "",
        _listing_attr(listing, "title") or "",
    ]
    return " ".join(str(p) for p in parts)


def _scrub_place_names(text: str) -> str:
    out = (text or "").lower()
    for phrase in _PLACE_NAME_PHRASES:
        out = out.replace(phrase, " ")
    return out


def order_canonical(codes: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in CANONICAL_DIRECTIONS:
        if code in codes and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def expand_adjacent_cardinal_pairs(codes: Sequence[str]) -> list[str]:
    """Add intercardinals implied by adjacent cardinal pairs (listing-side only)."""
    ordered = order_canonical(codes)
    present = set(ordered)
    extra = [
        inter
        for pair, inter in _INTERCARDINAL_FROM_CARDINALS.items()
        if pair <= present and inter not in present
    ]
    if not extra:
        return ordered
    return order_canonical(list(present) + extra)


def collapse_adjacent_cardinal_pairs(codes: Sequence[str]) -> list[str]:
    """Replace adjacent cardinal pairs with the composed intercardinal (display)."""
    present = set(order_canonical(codes))
    consumed: set[str] = set()
    for pair, inter in _INTERCARDINAL_FROM_CARDINALS.items():
        if pair <= present:
            present.add(inter)
            consumed |= pair
    present -= consumed
    return order_canonical(present)


def _direction_score_candidates(
    observed: Sequence[str],
    preferred: Sequence[str],
) -> list[tuple[str, float]]:
    """Per-observed scores for OR aggregation.

    Adjacent cardinals compose an intercardinal (east+south → SE). That composed
    facing uses full circular distance. The consumed walls still count as an
    exact match (east+south vs South = 100%) but must not leak a 3-step
    consolation vs the opposite diagonal (east+south vs NW would otherwise be 10%).
    """
    pref = order_canonical(preferred)
    expanded = expand_adjacent_cardinal_pairs(observed)
    if not expanded or not pref:
        return []
    composed = collapse_adjacent_cardinal_pairs(expanded)
    consumed = set(expanded) - set(composed)
    scored: list[tuple[str, float]] = []
    for o in composed:
        best_for_o: Optional[float] = None
        for p in pref:
            pair = pairwise_score(o, p)
            if pair is None:
                continue
            if best_for_o is None or pair > best_for_o:
                best_for_o = pair
        if best_for_o is not None:
            scored.append((o, best_for_o))
    for o in order_canonical(consumed):
        if o in pref:
            scored.append((o, 1.0))
    return scored


def canonicalize_token(token: str | None) -> Optional[str]:
    """Map a single token/phrase to a canonical code, or None if not a direction."""
    s = re.sub(r"[\s_]+", " ", (token or "").strip().lower())
    s = s.replace("–", "-").replace("—", "-")
    if not s:
        return None
    s = re.sub(r"[\.]+$", "", s)
    for suffix in (" facing", "-facing", " exposure", " exposed", " aspect", " oriented"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return _TOKEN_ALIASES.get(s)


def parse_preferred_directions(value: Any) -> list[str]:
    """Parse stored CSV, chat list, or labels into canonical codes (stable compass order)."""
    if value is None:
        return []
    tokens: list[str]
    if isinstance(value, (list, tuple, set)):
        tokens = [str(x) for x in value if x is not None and str(x).strip()]
    else:
        raw = str(value).strip()
        if not raw:
            return []
        tokens = [p for p in re.split(r"[,;/|]+", raw) if p.strip()]
        if len(tokens) <= 1:
            # Whole string may be "South and West" or "south-facing".
            from_text = parse_directions_from_free_text(raw)
            if from_text:
                return from_text
    found: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        code = canonicalize_token(tok)
        if code and code not in seen:
            seen.add(code)
            found.append(code)
    if found:
        return order_canonical(found)
    if not isinstance(value, (list, tuple, set)):
        return parse_directions_from_free_text(str(value))
    return []


def serialize_preferred_directions(dirs: Sequence[str] | None) -> str:
    return ",".join(order_canonical(parse_preferred_directions(list(dirs or []))))


def display_labels(dirs: Sequence[str] | None) -> list[str]:
    return [DIRECTION_LABELS[c] for c in order_canonical(dirs or []) if c in DIRECTION_LABELS]


def format_listing_facing(listing: Union[dict, Any], unknown: str = "—") -> str:
    """Compact inferred facing for table/export cells. Unknown listings use ``unknown``."""
    labels = display_labels(collapse_adjacent_cardinal_pairs(extract_listing_facings(listing)))
    if not labels:
        return unknown
    return ", ".join(labels)


def format_facing_suffix(dirs: Sequence[str] | None) -> str:
    labels = display_labels(dirs)
    if not labels:
        return ""
    return "Facing: " + ", ".join(labels)


def facing_phrase_for_semantic(dirs: Sequence[str] | None) -> str:
    labels = display_labels(dirs)
    if not labels:
        return ""
    facings = [f"{lab.lower()}-facing" for lab in labels]
    if len(facings) == 1:
        return facings[0]
    if len(facings) == 2:
        return f"{facings[0]} or {facings[1]}"
    return ", ".join(facings[:-1]) + f", or {facings[-1]}"


def _codes_from_compass_span(span: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _ALIAS_SCAN.finditer(span or ""):
        code = canonicalize_token(match.group(0))
        if code and code not in seen:
            seen.add(code)
            found.append(code)
    return found


def extract_facings_from_text(text: str) -> list[str]:
    """Extract listing facings; requires facing/exposure/aspect context."""
    scrubbed = _scrub_place_names(text or "")
    if not scrubbed.strip():
        return []
    seen: set[str] = set()
    found: list[str] = []

    def _add(codes: Sequence[str]) -> None:
        for code in codes:
            if code in DIRECTION_INDEX and code not in seen:
                seen.add(code)
                found.append(code)

    for rx in (_CONTEXT_TRAILING, _CONTEXT_LEADING, _HYPHEN_FACING):
        for match in rx.finditer(scrubbed):
            _add(_codes_from_compass_span(match.group(1)))
    for match in _LETTER_CONTEXT.finditer(scrubbed):
        letter = match.group(1) or match.group(2)
        code = canonicalize_token(letter)
        if code:
            _add([code])
    return order_canonical(found)


def extract_listing_facings(listing: Union[dict, Any]) -> list[str]:
    return expand_adjacent_cardinal_pairs(
        extract_facings_from_text(listing_text_blob_for_facing(listing))
    )


def parse_directions_from_free_text(text: str) -> list[str]:
    """Looser parse for user/chat preference text (bare names allowed after scrubbing)."""
    raw = (text or "").strip()
    if not raw:
        return []
    labeled = extract_facing_block(raw)
    body = labeled if labeled else raw
    contextual = extract_facings_from_text(body)
    scrubbed = _scrub_place_names(body)
    seen: set[str] = set(contextual)
    found: list[str] = list(contextual)
    for match in _ALIAS_SCAN.finditer(scrubbed):
        code = canonicalize_token(match.group(0))
        if code and code not in seen:
            seen.add(code)
            found.append(code)
    return order_canonical(found)


def extract_facing_block(text: str) -> str:
    """Return the body of a Facing:/Direction(s): labeled block, or empty."""
    if not (text or "").strip():
        return ""
    match = _FACING_BLOCK_RE.search(text)
    if not match:
        return ""
    return (match.group(1) or "").strip()


def chat_direction_override(
    preferences_text: str | None = None,
    explicit: Any = None,
) -> list[str]:
    """Directions for this scoring turn: explicit tool arg wins, else parse text."""
    if explicit not in (None, "", []):
        parsed = parse_preferred_directions(explicit)
        if parsed:
            return parsed
    return preferred_directions_from_preferences_text(preferences_text or "")


def preferred_directions_from_preferences_text(text: str) -> list[str]:
    """Parse chat/tool preferences_text for a facing override (empty = no override).

    A labeled Facing:/Direction(s): block is authoritative. Otherwise only
    facing/exposure phrases or a direction-only list are accepted — amenity text
    is not scanned for bare compass words.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    block = extract_facing_block(raw)
    if block:
        parsed = parse_preferred_directions(block)
        if parsed:
            return parsed
    remainder = strip_facing_block(raw)
    for sep in ("\n\nProximity:", "\nProximity:"):
        if sep in remainder:
            remainder = remainder.split(sep, 1)[0].strip()
    contextual = extract_facings_from_text(remainder)
    if contextual:
        return contextual
    return parse_preferred_directions(remainder)


def strip_facing_block(text: str) -> str:
    """Remove Facing:/Direction(s): suffix blocks from combined preferences_text."""
    s = (text or "").strip()
    if not s:
        return ""
    s = _FACING_BLOCK_RE.sub("", s)
    return s.strip()


def circular_step_distance(a: str, b: str) -> Optional[int]:
    if a not in DIRECTION_INDEX or b not in DIRECTION_INDEX:
        return None
    ia = DIRECTION_INDEX[a]
    ib = DIRECTION_INDEX[b]
    return min(abs(ia - ib), 8 - abs(ia - ib))


def pairwise_score(observed: str, preferred: str) -> Optional[float]:
    dist = circular_step_distance(observed, preferred)
    if dist is None:
        return None
    return STEP_SCORES[dist]


def best_direction_score(
    observed: Sequence[str],
    preferred: Sequence[str],
) -> Optional[float]:
    """OR: max circular similarity over observed × preferred. None if either side empty."""
    scored = _direction_score_candidates(observed, preferred)
    if not scored:
        return None
    return max(score for _, score in scored)


def best_matching_observed(
    observed: Sequence[str],
    preferred: Sequence[str],
) -> list[str]:
    """Observed facings that achieve the best pairwise score vs preferred."""
    scored = _direction_score_candidates(observed, preferred)
    if not scored:
        return []
    top = max(score for _, score in scored)
    return [code for code, score in scored if score == top]


def score_direction(
    listing: Union[dict, Any],
    preferred: Sequence[str] | None,
) -> Optional[float]:
    """Fifth match_score component. None when prefs unset or listing facing unknown."""
    pref = order_canonical(preferred or [])
    if not pref:
        return None
    observed = extract_listing_facings(listing)
    if not observed:
        return None
    return best_direction_score(observed, pref)


def evaluate_direction_criterion(
    listing: Union[dict, Any],
    preferred: Sequence[str] | None,
):
    """Single coverage checklist row for preferred facing."""
    from rental_search_agent.preference_criteria import _crit

    pref = order_canonical(preferred or [])
    required = ", ".join(display_labels(pref)) if pref else None
    if not pref:
        return _crit(
            "direction",
            "Facing",
            "unknown",
            None,
            group="direction",
            required=required,
            detail="Not set",
        )
    observed_codes = extract_listing_facings(listing)
    if not observed_codes:
        return _crit(
            "direction",
            "Facing",
            "unknown",
            None,
            group="direction",
            required=required,
            comparator="OR",
            detail="Not mentioned in listing description",
        )
    score = best_direction_score(observed_codes, pref)
    # Same composed label as the results table (e.g. east+south → South-East).
    observed = format_listing_facing(listing)
    if score is None:
        status = "unknown"
    elif score >= 0.99:
        status = "met"
    elif score <= 0.0:
        status = "unmet"
    else:
        status = "partial"
    return _crit(
        "direction",
        "Facing",
        status,
        None if score is None else float(score),
        group="direction",
        observed=observed,
        required=None,
        comparator=None,
        source="Inferred",
    )
