"""View-model for listing analysis: structured criteria, highlights, coverage counts.

Scoring formulas live in match_scoring / preference_criteria. This module only
shapes already-computed results for the Analyze UI so display and scoring share
the same resolved requirements (checklist built from EffectiveSearchPreferences).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from rental_search_agent.display_format import (
    criterion_source_help,
    criterion_source_label,
    format_count,
    format_criterion_comparison,
    format_duration,
    score_to_pct,
    split_listing_address,
)
from rental_search_agent.preference_criteria import AMENITY_FEATURES, match_amenity_feature
from rental_search_agent.scoring_config import DEFAULT_WEIGHTS, get_score_weights

COMPONENT_ORDER = ("structural", "proximity", "amenity", "direction", "semantic")

COMPONENT_HELP = {
    "structural": "Property basics such as price, size, bedrooms, bathrooms, and type.",
    "proximity": "Location-based criteria such as transit access and commute.",
    "amenity": "Building and unit features such as parking, balcony, and storage.",
    "direction": "Preferred unit facing or exposure (north, south, east, west, and diagonals).",
    "semantic": (
        "Semantic similarity compares the listing text with broader qualitative "
        "preferences. It is experimental and can be lower when listing text is "
        "sparse; explicit criteria are scored separately."
    ),
}

COMPONENT_CAPTION = {
    "structural": "Price, size, beds, baths",
    "proximity": "Location & commute",
    "amenity": "Building & unit features",
    "direction": "Facing & exposure",
    "semantic": "Listing details",
}

GROUP_ORDER = ("structural", "proximity", "amenity", "direction")
GROUP_TITLES = {
    "structural": "Structural",
    "proximity": "Proximity",
    "amenity": "Amenities",
    "direction": "Facing",
}

COMPONENT_TITLES = {
    **GROUP_TITLES,
    "semantic": "Semantic",
}

STATUS_MARKER = {
    "met": "✓",
    "partial": "~",
    "unmet": "✕",
    "unknown": "?",
}

STATUS_LABEL = {
    "met": "Met",
    "partial": "Close",
    "unmet": "Unmet",
    "unknown": "Not mentioned",
}

# Decorative highlight icon keys (presentation-only; PNG assets in ui_assets).
# "generic" is the fallback; "other" is accepted as an alias.
HIGHLIGHT_ICON_KEYS = ("commute", "space", "budget", "parking", "generic")

# Feature-chip icon keys used by the AI listing summary row.
SUMMARY_FEATURE_ICON_KEYS = ("beds", "baths", "transit", "parking", "amenity")

MAX_SUMMARY_FEATURES = 5

# Amenity chip priority: only confirmed (met) amenities become chips.
_SUMMARY_AMENITY_CHIP_LABELS: tuple[tuple[str, str], ...] = (
    ("balcony", "Balcony"),
    ("laundry", "In-suite laundry"),
    ("parking", "Parking"),
    ("ensuite_bathroom", "Ensuite bathroom"),
    ("dishwasher", "Dishwasher"),
    ("gym", "Gym"),
    ("storage", "Storage"),
    ("ac", "Air conditioning"),
    ("pets", "Pet-friendly"),
    ("elevator", "Elevator"),
    ("walk_in_closet", "Walk-in closet"),
    ("furnished", "Furnished"),
    ("pool", "Pool"),
    ("patio", "Patio"),
    ("deck", "Deck"),
)

_TRANSIT_LOCATION_HINTS = ("nearest transit", "transit station", "skytrain", "train station")


@dataclass
class CriteriaRow:
    id: str
    group: str
    status: str
    name: str
    observed: Optional[str]
    required: Optional[str]
    comparator: Optional[str]
    source: Optional[str]
    source_label: str
    source_help: Optional[str]
    comparison_text: str
    marker: str
    status_label: str


@dataclass
class Highlight:
    title: str
    body: str
    icon_key: str = "generic"


@dataclass(frozen=True)
class SummaryFeature:
    """Compact fact chip for the AI listing summary row."""

    label: str
    icon_key: str = "amenity"


@dataclass
class AnalysisView:
    headline: str
    locality: str
    match_pct: Optional[int]
    strength_label: str
    strength_blurb: str
    evaluated_count: int
    total_count: int
    unknown_count: int
    unmet_count: int
    components: dict[str, Optional[float]]
    included: list[str]
    weights_used: dict[str, float]
    configured_weights: dict[str, float]
    criteria_by_group: dict[str, list[CriteriaRow]] = field(default_factory=dict)
    highlights: list[Highlight] = field(default_factory=list)
    open_questions: list[CriteriaRow] = field(default_factory=list)
    unmet: list[CriteriaRow] = field(default_factory=list)
    show_semantic_note: bool = False
    ai_listing_summary: Optional[str] = None
    original_listing_description: Optional[str] = None
    summary_features: list[SummaryFeature] = field(default_factory=list)
    summary_is_ai_generated: bool = False


def match_strength(pct: Optional[int]) -> tuple[str, str]:
    if pct is None:
        return (
            "Match unavailable",
            "Not enough scored components were available to compute an overall match.",
        )
    if pct >= 90:
        return (
            "Strong match",
            "This property aligns closely with your search criteria.",
        )
    if pct >= 75:
        return (
            "Good match",
            "This property aligns well with your search criteria.",
        )
    if pct >= 60:
        return (
            "Fair match",
            "This property partially matches your search criteria.",
        )
    return (
        "Weaker match",
        "This property diverges from several of your search criteria.",
    )


def _infer_group(item: dict) -> str:
    group = (item.get("group") or "").strip()
    if group in GROUP_ORDER:
        return group
    cid = str(item.get("id") or "")
    if cid.startswith("proximity:"):
        return "proximity"
    if cid in ("budget", "beds", "baths", "sqft", "house_category", "den"):
        return "structural"
    return "amenity"


def checklist_item_to_row(item: dict) -> CriteriaRow:
    status = str(item.get("status") or "unknown")
    if status not in STATUS_MARKER:
        status = "unknown"
    name = str(item.get("name") or item.get("label") or item.get("id") or "Criterion").strip()
    observed = item.get("observed")
    required = item.get("required")
    comparator = item.get("comparator")
    detail = item.get("detail")
    if status == "unknown":
        comparison = (detail or "").strip() or "Not mentioned"
        observed_display = None
    else:
        comparison = format_criterion_comparison(
            observed if observed else None,
            comparator if comparator else None,
            required if required else None,
            unknown_text=(detail or "Not available"),
        )
        observed_display = str(observed).strip() if observed else None
    source = item.get("source")
    source_s = str(source).strip() if source else None
    return CriteriaRow(
        id=str(item.get("id") or name),
        group=_infer_group(item),
        status=status,
        name=name,
        observed=observed_display,
        required=str(required).strip() if required else None,
        comparator=str(comparator).strip() if comparator else None,
        source=source_s,
        source_label=criterion_source_label(source_s),
        source_help=criterion_source_help(source_s),
        comparison_text=comparison,
        marker=STATUS_MARKER[status],
        status_label=STATUS_LABEL[status],
    )


def _minutes_from_observed(text: str) -> Optional[int]:
    head = (text or "").strip().split()
    if not head:
        return None
    try:
        return int(round(float(head[0])))
    except (TypeError, ValueError):
        return None


def _build_highlights(rows: Sequence[CriteriaRow]) -> list[Highlight]:
    """2–4 decision-relevant strengths from structured criteria, not a full checklist repeat."""
    met = [r for r in rows if r.status == "met"]
    by_id = {r.id: r for r in met}
    out: list[Highlight] = []

    walk = next(
        (r for r in met if r.id.startswith("proximity:") and "walk" in r.name.lower()),
        None,
    )
    drive = next(
        (r for r in met if r.id.startswith("proximity:") and "drive" in r.name.lower()),
        None,
    )
    if walk and walk.observed and drive and drive.observed:
        walk_loc = walk.name.replace("Walk to ", "", 1)
        loc = drive.name.replace("Drive to ", "", 1)
        out.append(Highlight(
            "Convenient commute",
            f"{walk.observed} to {walk_loc}, {drive.observed} to {loc}.",
            icon_key="commute",
        ))
    elif walk and walk.observed:
        loc = walk.name.replace("Walk to ", "", 1)
        mins = _minutes_from_observed(walk.observed)
        title = "Excellent transit access" if mins is not None and mins <= 5 else "Transit access"
        out.append(Highlight(
            title,
            f"{walk.observed} to {loc}, within your {walk.required or 'limit'}.",
            icon_key="commute",
        ))
    elif drive and drive.observed:
        loc = drive.name.replace("Drive to ", "", 1)
        suffix = f", within your {drive.required} limit." if drive.required else "."
        out.append(Highlight(
            "Convenient commute",
            f"Approximately {drive.observed} to {loc}{suffix}",
            icon_key="commute",
        ))

    beds = by_id.get("beds")
    baths = by_id.get("baths")
    size = by_id.get("sqft")
    space_bits = []
    if beds and beds.observed:
        space_bits.append(f"{beds.observed} bedrooms")
    if baths and baths.observed:
        space_bits.append(f"{baths.observed} bathrooms")
    if size and size.observed:
        space_bits.append(size.observed)
    if space_bits and len(out) < 4:
        out.append(Highlight("Meets your space needs", ", ".join(space_bits) + ".", icon_key="space"))

    budget = by_id.get("budget")
    if budget and budget.observed and budget.required and len(out) < 4:
        cmp_ = budget.comparator or "≤"
        out.append(Highlight(
            "Within budget",
            f"{budget.observed} {cmp_} {budget.required}",
            icon_key="budget",
        ))

    parking = by_id.get("parking")
    if parking and len(out) < 4:
        detail = parking.observed or "Yes"
        out.append(Highlight("Parking", f"Parking is listed ({detail}).", icon_key="parking"))

    return out[:4]


def _semantic_note(components: dict[str, Optional[float]], included: Sequence[str]) -> bool:
    """True when Semantic is low relative to other components (tooltip affordance only)."""
    if "semantic" not in included:
        return False
    sem = components.get("semantic")
    if sem is None:
        return False
    others = [
        components.get(k)
        for k in ("structural", "proximity", "amenity", "direction")
        if k in included and components.get(k) is not None
    ]
    if not others:
        return float(sem) < 0.75
    return float(sem) < 0.75 and max(float(x) for x in others) >= 0.9


_MAX_SUMMARY_CHARS = 900


def normalize_ai_listing_summary(raw: Any) -> Optional[str]:
    """Sanitize listing summary text; return None when empty or unusable.

    Applied on LLM write and again on view resolve so cached/API payloads stay bounded.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Drop accidental markdown headings the model may still emit.
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    text = "\n".join(lines).strip()
    if not text:
        return None
    if len(text) > _MAX_SUMMARY_CHARS:
        text = text[:_MAX_SUMMARY_CHARS].rstrip() + "…"
    return text


def listing_original_description(listing: dict | None) -> Optional[str]:
    """Canonical original listing remarks/description from the listing model.

    Maps ``Listing.description`` (PublicRemarks / Description). Empty strings become None.
    """
    if not listing:
        return None
    raw = listing.get("description")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _format_beds_chip_label(listing: dict) -> Optional[str]:
    display = str(listing.get("bedrooms_display") or "").strip()
    if display:
        return f"{display} beds"
    beds = listing.get("bedrooms")
    if beds is None or beds == "":
        return None
    label = format_count(beds, unavailable="")
    if not label:
        return None
    try:
        n = float(beds)
        unit = "bed" if n == 1 else "beds"
    except (TypeError, ValueError):
        unit = "beds"
    return f"{label} {unit}"


def _format_baths_chip_label(listing: dict) -> Optional[str]:
    baths = listing.get("bathrooms")
    if baths is None or baths == "":
        return None
    label = format_count(baths, unavailable="")
    if not label:
        return None
    try:
        n = float(baths)
        unit = "bath" if n == 1 else "baths"
    except (TypeError, ValueError):
        unit = "baths"
    return f"{label} {unit}"


def _is_transit_location(location: str) -> bool:
    loc = (location or "").strip().lower()
    return any(hint in loc for hint in _TRANSIT_LOCATION_HINTS)


def _transit_chip_label(listing: dict) -> Optional[str]:
    prox = listing.get("proximity")
    if not isinstance(prox, dict) or not prox:
        return None
    best_min: Optional[float] = None
    for rule_key, val in prox.items():
        location = str(rule_key).split("|", 1)[0].strip()
        if not _is_transit_location(location):
            continue
        if not isinstance(val, dict):
            continue
        duration = val.get("duration_min")
        if duration is None or duration == "":
            continue
        try:
            mins = float(duration)
        except (TypeError, ValueError):
            continue
        if best_min is None or mins < best_min:
            best_min = mins
    if best_min is None:
        return None
    dur = format_duration(best_min, unavailable="")
    if not dur:
        return None
    return f"{dur} to transit"


def _amenity_by_id() -> dict[str, Any]:
    return {feat.id: feat for feat in AMENITY_FEATURES}


def build_summary_features(listing: dict | None) -> list[SummaryFeature]:
    """Derive up to five compact feature chips from structured listing evidence only.

    Unknown or unsupported features are omitted — never invent chips.
    """
    listing = listing or {}
    features: list[SummaryFeature] = []

    beds = _format_beds_chip_label(listing)
    if beds:
        features.append(SummaryFeature(label=beds, icon_key="beds"))
    baths = _format_baths_chip_label(listing)
    if baths:
        features.append(SummaryFeature(label=baths, icon_key="baths"))
    transit = _transit_chip_label(listing)
    if transit:
        features.append(SummaryFeature(label=transit, icon_key="transit"))

    amenity_lookup = _amenity_by_id()
    for feat_id, chip_label in _SUMMARY_AMENITY_CHIP_LABELS:
        if len(features) >= MAX_SUMMARY_FEATURES:
            break
        feat = amenity_lookup.get(feat_id)
        if feat is None:
            continue
        result = match_amenity_feature(listing, feat)
        if result.status != "met":
            continue
        # Avoid duplicating a beds-style den chip when bedrooms_display already covers it.
        if feat_id == "den" and listing.get("bedrooms_display"):
            continue
        icon = "parking" if feat_id == "parking" else "amenity"
        features.append(SummaryFeature(label=chip_label, icon_key=icon))

    return features[:MAX_SUMMARY_FEATURES]


def deterministic_listing_summary(
    listing: dict | None,
    features: Sequence[SummaryFeature] | None = None,
) -> Optional[str]:
    """Conservative fallback summary from structured fields when LLM text is unavailable."""
    listing = listing or {}
    features = list(features) if features is not None else build_summary_features(listing)

    ptype = (listing.get("house_category") or "").strip() or "property"
    ptype_l = ptype.lower()

    bed_disp = str(listing.get("bedrooms_display") or "").strip()
    beds_n = format_count(listing.get("bedrooms"), unavailable="")
    baths_n = format_count(listing.get("bathrooms"), unavailable="")

    space_bits: list[str] = []
    if bed_disp:
        space_bits.append(f"{bed_disp} bedroom")
    elif beds_n:
        space_bits.append(f"{beds_n}-bedroom")
    if baths_n:
        space_bits.append(f"{baths_n}-bathroom")

    if space_bits:
        lead = f"{', '.join(space_bits)} {ptype_l}"
    else:
        lead = ptype_l
    sentences = [lead[0].upper() + lead[1:] + "."]

    amenity_labels = [
        f.label for f in features if f.icon_key in ("amenity", "parking") and f.label
    ]
    transit_labels = [f.label for f in features if f.icon_key == "transit"]

    if transit_labels and amenity_labels:
        am_text = (
            amenity_labels[0].lower()
            if len(amenity_labels) == 1
            else (
                ", ".join(a.lower() for a in amenity_labels[:-1])
                + f", and {amenity_labels[-1].lower()}"
            )
        )
        sentences.append(
            f"Transit access appears strong ({transit_labels[0]}) and the listing includes {am_text}."
        )
    elif transit_labels:
        sentences.append(f"Transit access appears strong ({transit_labels[0]}).")
    elif amenity_labels:
        am_text = (
            amenity_labels[0].lower()
            if len(amenity_labels) == 1
            else (
                ", ".join(a.lower() for a in amenity_labels[:-1])
                + f", and {amenity_labels[-1].lower()}"
            )
        )
        sentences.append(f"The listing includes {am_text}.")

    if not listing_original_description(listing):
        sentences.append(
            "Some listing details are not specified in the available data; "
            "verify with the listing agent."
        )
    elif not amenity_labels and not transit_labels and (beds_n or baths_n or ptype):
        sentences.append(
            "Important amenities are not clearly indicated from structured listing fields."
        )

    text = " ".join(s.strip() for s in sentences if s.strip())
    return text or None


def resolve_ai_listing_summary(
    listing: dict | None,
    result: dict | None,
    features: Sequence[SummaryFeature] | None = None,
) -> tuple[Optional[str], bool]:
    """Return (summary_text, is_ai_generated).

    Prefers the LLM field from the analysis result; falls back to a deterministic
    summary from structured listing data. Never raises.
    """
    result = result or {}
    text = normalize_ai_listing_summary(result.get("ai_listing_summary"))
    if text:
        return text, True
    fallback = deterministic_listing_summary(listing, features)
    return fallback, False


def build_analysis_view(listing: dict, result: dict) -> AnalysisView:
    """Assemble the Analyze UI view-model from a listing + analysis result."""
    listing = listing or {}
    result = result or {}
    headline, locality = split_listing_address(
        listing.get("address"),
        listing.get("postal_code"),
    )
    if not headline:
        headline = str(listing.get("id") or "Listing")

    match_pct: Any = result.get("match_score_pct")
    if match_pct is not None:
        try:
            match_pct = int(match_pct)
        except (TypeError, ValueError):
            match_pct = score_to_pct(match_pct)
    if match_pct is None:
        match_pct = score_to_pct(listing.get("match_score"))

    strength_label, strength_blurb = match_strength(match_pct)

    breakdown = result.get("score_breakdown") or listing.get("score_breakdown") or {}
    components = dict(breakdown.get("components") or {})
    included = list(
        breakdown.get("included")
        or [k for k in COMPONENT_ORDER if components.get(k) is not None]
    )
    weights_used = dict(breakdown.get("weights_used") or {})
    try:
        configured_weights = dict(get_score_weights())
    except Exception:
        configured_weights = dict(DEFAULT_WEIGHTS)

    raw_items = breakdown.get("checklist") or []
    rows = [checklist_item_to_row(it) for it in raw_items if isinstance(it, dict)]

    criteria_by_group: dict[str, list[CriteriaRow]] = {g: [] for g in GROUP_ORDER}
    for row in rows:
        criteria_by_group.setdefault(row.group, []).append(row)

    unknown = [r for r in rows if r.status == "unknown"]
    unmet = [r for r in rows if r.status == "unmet"]
    evaluated = [r for r in rows if r.status != "unknown"]

    summary_features = build_summary_features(listing)
    ai_summary, summary_is_ai = resolve_ai_listing_summary(
        listing, result, summary_features
    )

    return AnalysisView(
        headline=headline,
        locality=locality,
        match_pct=match_pct,
        strength_label=strength_label,
        strength_blurb=strength_blurb,
        evaluated_count=len(evaluated),
        total_count=len(rows),
        unknown_count=len(unknown),
        unmet_count=len(unmet),
        components=components,
        included=included,
        weights_used=weights_used,
        configured_weights=configured_weights,
        criteria_by_group=criteria_by_group,
        highlights=_build_highlights(rows),
        open_questions=unknown,
        unmet=unmet,
        show_semantic_note=_semantic_note(components, included),
        ai_listing_summary=ai_summary,
        original_listing_description=listing_original_description(listing),
        summary_features=summary_features,
        summary_is_ai_generated=summary_is_ai,
    )


def listing_property_type(listing: dict) -> Optional[str]:
    """Building format from the listing (house_category), not the broader Property.Type."""
    house = (listing.get("house_category") or "").strip()
    return house or None


def listing_property_category(listing: dict) -> Optional[str]:
    """Broader Realtor.ca Property.Type when it differs from house_category."""
    cat = (listing.get("property_category") or "").strip()
    house = listing_property_type(listing) or ""
    if cat and cat.lower() != house.lower():
        return cat
    return None


def format_weight_pct(weight: float) -> str:
    return f"{int(round(float(weight) * 100))}%"


def weighted_score_line(weights_used: dict[str, float] | None) -> Optional[str]:
    """Compact weighted-score composition from actual weights used for this listing."""
    if not weights_used:
        return None
    bits = []
    labels = {
        "structural": "Structural",
        "proximity": "Proximity",
        "amenity": "Amenities",
        "direction": "Facing",
        "semantic": "Semantic",
    }
    for key in COMPONENT_ORDER:
        if key not in weights_used:
            continue
        bits.append(f"{labels.get(key, key.capitalize())} {format_weight_pct(weights_used[key])}")
    if not bits:
        return None
    return "Weighted score · " + " · ".join(bits)
