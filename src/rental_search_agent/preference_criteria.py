"""Build preference checklists and amenity feature matchers for multi-metric scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Sequence, Union

from rental_search_agent.display_format import (
    format_count,
    format_currency,
    format_duration,
    format_sqft,
    proximity_criterion_name,
)
from rental_search_agent.filtering import _house_category_matches
from rental_search_agent.preference_resolution import EffectiveSearchPreferences

CriterionStatus = Literal["met", "partial", "unmet", "unknown"]
CriterionGroup = Literal["structural", "proximity", "amenity"]


@dataclass
class CriterionResult:
    id: str
    label: str
    status: CriterionStatus
    score: Optional[float]  # None when unknown (excluded from averages)
    weight: float = 1.0
    group: CriterionGroup = "structural"
    name: str = ""
    observed: Optional[str] = None
    required: Optional[str] = None
    comparator: Optional[str] = None
    source: Optional[str] = None  # MLS | Calculated | Inferred; None when unknown/unavailable
    detail: Optional[str] = None  # e.g. "Not mentioned", "Yes"


@dataclass
class AmenityFeature:
    id: str
    label: str
    patterns: tuple[str, ...]


# Lightweight vocabulary: qualitative prefs are scanned for these asks; listings matched via
# structured fields + case-insensitive substring search on amenities/description.
# A hit in remarks is met; a miss is unmet when description is present, unknown when it is not.
AMENITY_FEATURES: tuple[AmenityFeature, ...] = (
    AmenityFeature("parking", "Parking", ("parking", "garage", "underground parking", "carport")),
    AmenityFeature("balcony", "Balcony", ("balcony", "patio", "terrace", "deck")),
    AmenityFeature("gym", "Gym", ("gym", "fitness", "exercise room")),
    AmenityFeature("pets", "Pet-friendly", ("pet friendly", "pet-friendly", "pets allowed", "cats ok", "dogs ok", "pets ok")),
    AmenityFeature("laundry", "In-suite laundry", ("in-suite laundry", "in suite laundry", "washer", "dryer", "laundry")),
    AmenityFeature("dishwasher", "Dishwasher", ("dishwasher",)),
    AmenityFeature("ac", "Air conditioning", ("air conditioning", "a/c", " aircon", "central air")),
    AmenityFeature("storage", "Storage", ("storage", "locker")),
    AmenityFeature("ev", "EV charger", ("ev charger", "ev charging", "electric vehicle")),
    AmenityFeature("elevator", "Elevator", ("elevator", "lift")),
    AmenityFeature("furnished", "Furnished", ("furnished",)),
    AmenityFeature("den", "Den", ("den", "flex room", "flex space")),
)


def extract_amenity_features(qualitative_text: str) -> List[AmenityFeature]:
    """Return amenity features mentioned in qualitative preference text (deterministic)."""
    text = (qualitative_text or "").strip().lower()
    if not text:
        return []
    found: List[AmenityFeature] = []
    for feat in AMENITY_FEATURES:
        for pat in feat.patterns:
            if pat in text:
                found.append(feat)
                break
    return found


def _listing_attr(listing: Union[dict, Any], key: str) -> Any:
    if isinstance(listing, dict):
        return listing.get(key)
    return getattr(listing, key, None)


def _listing_text_blob_for_amenities(listing: Union[dict, Any]) -> str:
    parts = [
        _listing_attr(listing, "description") or "",
        _listing_attr(listing, "ammenities") or "",
        _listing_attr(listing, "nearby_ammenities") or "",
        _listing_attr(listing, "parking_type") or "",
        _listing_attr(listing, "title") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def _has_listing_description(listing: Union[dict, Any]) -> bool:
    """True when the listing has remarks we can search for qualitative features."""
    return bool(str(_listing_attr(listing, "description") or "").strip())


def _amenity_not_found(feature: AmenityFeature, listing: Union[dict, Any]) -> CriterionResult:
    """Absence of a requested amenity: unmet when remarks exist, else unknown.

    When PublicRemarks were always empty, 'not mentioned' had to be unknown so
    empty text did not drag amenity/match scores down. With fetchDetails, a real
    description is evidence: if the user asked for a feature and the remarks
    never mention it, treat that as unmet so description actually affects ranking.
    """
    if _has_listing_description(listing):
        return _crit(
            feature.id,
            feature.label,
            "unmet",
            0.0,
            group="amenity",
            observed="No",
            source="Inferred",
            detail="Not mentioned in listing description",
        )
    return _crit(
        feature.id,
        feature.label,
        "unknown",
        None,
        group="amenity",
        detail="Not mentioned",
    )


def _crit(
    cid: str,
    name: str,
    status: CriterionStatus,
    score: Optional[float],
    *,
    group: CriterionGroup = "structural",
    observed: Optional[str] = None,
    required: Optional[str] = None,
    comparator: Optional[str] = None,
    source: Optional[str] = None,
    detail: Optional[str] = None,
    label: Optional[str] = None,
) -> CriterionResult:
    """Build a checklist item with display fields. Scoring still uses status/score only."""
    return CriterionResult(
        id=cid,
        label=label or name,
        status=status,
        score=score,
        group=group,
        name=name,
        observed=observed,
        required=required,
        comparator=comparator,
        source=source,
        detail=detail,
    )


def match_amenity_feature(listing: Union[dict, Any], feature: AmenityFeature) -> CriterionResult:
    """Match one amenity feature against structured fields + listing text."""
    if feature.id == "parking":
        spaces = _listing_attr(listing, "parking_spaces")
        ptype = (_listing_attr(listing, "parking_type") or "").strip()
        if spaces is not None:
            try:
                n = int(spaces)
                if n >= 1:
                    observed = format_count(n) if n > 1 else "Yes"
                    return _crit(
                        feature.id, feature.label, "met", 1.0,
                        group="amenity", observed=observed, source="MLS",
                    )
                return _crit(
                    feature.id, feature.label, "unmet", 0.0,
                    group="amenity", observed="No", source="MLS",
                )
            except (TypeError, ValueError):
                pass
        if ptype:
            return _crit(
                feature.id, feature.label, "met", 1.0,
                group="amenity", observed=ptype, source="MLS",
            )
        text = _listing_text_blob_for_amenities(listing)
        if any(p in text for p in feature.patterns):
            return _crit(
                feature.id, feature.label, "met", 1.0,
                group="amenity", observed="Yes", source="Inferred",
            )
        return _amenity_not_found(feature, listing)

    if feature.id == "den":
        has_den = _listing_attr(listing, "has_den")
        if has_den is True:
            return _crit(feature.id, feature.label, "met", 1.0, group="amenity", observed="Yes", source="MLS")
        if has_den is False:
            return _crit(feature.id, feature.label, "unmet", 0.0, group="amenity", observed="No", source="MLS")
        text = _listing_text_blob_for_amenities(listing)
        bed_disp = str(_listing_attr(listing, "bedrooms_display") or "").lower()
        if "+ den" in bed_disp or "den" in text:
            source = "MLS" if "+ den" in bed_disp else "Inferred"
            return _crit(feature.id, feature.label, "met", 1.0, group="amenity", observed="Yes", source=source)
        return _amenity_not_found(feature, listing)

    text = _listing_text_blob_for_amenities(listing)
    if any(p in text for p in feature.patterns):
        return _crit(
            feature.id, feature.label, "met", 1.0,
            group="amenity", observed="Yes", source="Inferred",
        )
    return _amenity_not_found(feature, listing)


def _proximity_rule_key(rule: dict) -> str:
    location = (rule.get("location") or "").strip()
    mode = (rule.get("mode") or "").strip()
    return f"{location}|{mode}"


def evaluate_proximity_criterion(
    listing: Union[dict, Any],
    rule: dict,
) -> CriterionResult:
    location = (rule.get("location") or "").strip() or "location"
    mode = (rule.get("mode") or "").strip() or "travel"
    max_minutes = rule.get("max_minutes")
    name = proximity_criterion_name(mode, location)
    cid = f"proximity:{_proximity_rule_key(rule)}"
    required = format_duration(max_minutes) if max_minutes is not None else None

    def _unknown() -> CriterionResult:
        return _crit(
            cid, name, "unknown", None,
            group="proximity", required=required, comparator="≤",
            detail="Not available",
        )

    if max_minutes is None:
        return _unknown()

    prox = _listing_attr(listing, "proximity")
    if not isinstance(prox, dict):
        return _unknown()
    key = _proximity_rule_key(rule)
    val = prox.get(key)
    if val is None or not isinstance(val, dict):
        return _unknown()
    duration = val.get("duration_min")
    if duration is None:
        return _unknown()
    try:
        d = float(duration)
        m = float(max_minutes)
    except (TypeError, ValueError):
        return _unknown()
    observed = format_duration(d)
    if m <= 0:
        status: CriterionStatus = "met" if d <= 0 else "unmet"
        return _crit(
            cid, name, status, 1.0 if status == "met" else 0.0,
            group="proximity", observed=observed, required=required, comparator="≤",
            source="Calculated",
        )
    if d <= m:
        return _crit(
            cid, name, "met", 1.0,
            group="proximity", observed=observed, required=required, comparator="≤",
            source="Calculated",
        )
    if d <= m * 1.25:
        return _crit(
            cid, name, "partial", 0.6,
            group="proximity", observed=observed, required=required, comparator="≤",
            source="Calculated",
        )
    return _crit(
        cid, name, "unmet", 0.0,
        group="proximity", observed=observed, required=required, comparator="≤",
        source="Calculated",
    )


def build_structural_checklist(
    prefs: EffectiveSearchPreferences,
    listing: Union[dict, Any],
) -> List[CriterionResult]:
    """Deterministic checklist items for structural targets."""
    results: List[CriterionResult] = []

    if prefs.budget_max is not None:
        required = format_currency(prefs.budget_max)
        price = _listing_attr(listing, "price")
        if price is None:
            results.append(_crit(
                "budget", "Price", "unknown", None,
                required=required, comparator="≤", detail="Not available",
            ))
        else:
            try:
                p = float(price)
                observed = format_currency(p)
                if p <= prefs.budget_max:
                    status: CriterionStatus = "met"
                    score: Optional[float] = 1.0
                elif p <= prefs.budget_max * 1.1:
                    status, score = "partial", 0.5
                else:
                    status, score = "unmet", 0.0
                results.append(_crit(
                    "budget", "Price", status, score,
                    observed=observed, required=required, comparator="≤", source="MLS",
                ))
            except (TypeError, ValueError):
                results.append(_crit(
                    "budget", "Price", "unknown", None,
                    required=required, comparator="≤", detail="Not available",
                ))

    if prefs.min_bedrooms is not None or prefs.max_bedrooms is not None:
        beds = _listing_attr(listing, "bedrooms")
        if prefs.min_bedrooms is not None and prefs.max_bedrooms is not None:
            if prefs.min_bedrooms == prefs.max_bedrooms:
                required = format_count(prefs.min_bedrooms)
                comparator: Optional[str] = "≥"
            else:
                required = f"{format_count(prefs.min_bedrooms)}–{format_count(prefs.max_bedrooms)}"
                comparator = None
        elif prefs.min_bedrooms is not None:
            required = format_count(prefs.min_bedrooms)
            comparator = "≥"
        else:
            required = format_count(prefs.max_bedrooms)
            comparator = "≤"
        if beds is None:
            results.append(_crit(
                "beds", "Bedrooms", "unknown", None,
                required=required, comparator=comparator, detail="Not available",
            ))
        else:
            b = int(beds)
            observed = format_count(b)
            ok_min = prefs.min_bedrooms is None or b >= prefs.min_bedrooms
            ok_max = prefs.max_bedrooms is None or b <= prefs.max_bedrooms
            if ok_min and ok_max:
                status, score = "met", 1.0
            elif prefs.min_bedrooms is not None and b == prefs.min_bedrooms - 1:
                status, score = "partial", 0.5
            else:
                status, score = "unmet", 0.0
            results.append(_crit(
                "beds", "Bedrooms", status, score,
                observed=observed, required=required,
                comparator=comparator, source="MLS",
            ))

    if prefs.min_bathrooms is not None:
        baths = _listing_attr(listing, "bathrooms")
        required = format_count(prefs.min_bathrooms)
        if baths is None:
            results.append(_crit(
                "baths", "Bathrooms", "unknown", None,
                required=required, comparator="≥", detail="Not available",
            ))
        else:
            try:
                ba = float(baths)
                observed = format_count(ba)
                if ba >= prefs.min_bathrooms:
                    status, score = "met", 1.0
                elif ba >= prefs.min_bathrooms - 0.5:
                    status, score = "partial", 0.6
                else:
                    status, score = "unmet", 0.0
                results.append(_crit(
                    "baths", "Bathrooms", status, score,
                    observed=observed, required=required, comparator="≥", source="MLS",
                ))
            except (TypeError, ValueError):
                results.append(_crit(
                    "baths", "Bathrooms", "unknown", None,
                    required=required, comparator="≥", detail="Not available",
                ))

    if prefs.min_sqft is not None:
        sqft = _listing_attr(listing, "sqft")
        required = format_sqft(prefs.min_sqft)
        if sqft is None:
            results.append(_crit(
                "sqft", "Size", "unknown", None,
                required=required, comparator="≥", detail="Not available",
            ))
        else:
            try:
                s = float(sqft)
                observed = format_sqft(s)
                if s >= prefs.min_sqft:
                    status, score = "met", 1.0
                elif s >= prefs.min_sqft * 0.9:
                    status, score = "partial", 0.6
                else:
                    status, score = "unmet", 0.0
                results.append(_crit(
                    "sqft", "Size", status, score,
                    observed=observed, required=required, comparator="≥", source="MLS",
                ))
            except (TypeError, ValueError):
                results.append(_crit(
                    "sqft", "Size", "unknown", None,
                    required=required, comparator="≥", detail="Not available",
                ))

    if prefs.require_den:
        den = match_amenity_feature(listing, AmenityFeature("den", "Den", ("den",)))
        den.group = "structural"
        results.append(den)

    if prefs.house_categories:
        cat = (_listing_attr(listing, "house_category") or "").strip()
        required = " or ".join(prefs.house_categories)
        if not cat:
            results.append(_crit(
                "house_category", "Property type", "unknown", None,
                required=required, detail="Not available",
            ))
        else:
            if _house_category_matches(cat, prefs.house_categories):
                status, score = "met", 1.0
            else:
                status, score = "unmet", 0.0
            results.append(_crit(
                "house_category", "Property type", status, score,
                observed=cat, required=required, source="MLS",
            ))

    return results


def evaluate_coverage(
    prefs: EffectiveSearchPreferences,
    listing: Union[dict, Any],
    proximity_rules: Optional[Sequence[dict]] = None,
    amenity_features: Optional[Sequence[AmenityFeature]] = None,
) -> tuple[Optional[float], List[CriterionResult]]:
    """Coverage = weighted mean of known criterion scores; unknown excluded from denominator."""
    items: List[CriterionResult] = []
    items.extend(build_structural_checklist(prefs, listing))
    for rule in proximity_rules or []:
        if isinstance(rule, dict):
            items.append(evaluate_proximity_criterion(listing, rule))
    for feat in amenity_features or []:
        # Avoid double-counting den if already required structurally
        if feat.id == "den" and prefs.require_den:
            continue
        items.append(match_amenity_feature(listing, feat))

    known = [c for c in items if c.score is not None]
    if not known:
        return None, items
    total_w = sum(c.weight for c in known)
    if total_w <= 0:
        return None, items
    score = sum(c.weight * float(c.score) for c in known) / total_w
    return max(0.0, min(1.0, score)), items


def _floor_score(value: float, target: float, soft_ratio: float = 0.5) -> float:
    """1.0 if value >= target; linear decay to 0 at soft_ratio * target."""
    if target <= 0:
        return 1.0
    if value >= target:
        return 1.0
    floor = target * soft_ratio
    if value <= floor:
        return 0.0
    return (value - floor) / (target - floor)


def score_structural(
    prefs: EffectiveSearchPreferences,
    listing: Union[dict, Any],
) -> Optional[float]:
    """Graded structural fit; omit when no structural targets set."""
    parts: List[float] = []

    if prefs.budget_max is not None and prefs.budget_max > 0:
        price = _listing_attr(listing, "price")
        if price is not None:
            try:
                p = float(price)
                if p <= prefs.budget_max:
                    parts.append(1.0)
                else:
                    # Decay to 0 at 1.25× budget (at-budget still scores 1.0)
                    over = (p - prefs.budget_max) / (prefs.budget_max * 0.25)
                    parts.append(max(0.0, 1.0 - over))
            except (TypeError, ValueError):
                pass

    if prefs.min_bedrooms is not None or prefs.max_bedrooms is not None:
        beds = _listing_attr(listing, "bedrooms")
        if beds is not None:
            b = float(beds)
            if prefs.min_bedrooms is not None and prefs.max_bedrooms is not None:
                if prefs.min_bedrooms <= b <= prefs.max_bedrooms:
                    parts.append(1.0)
                elif b < prefs.min_bedrooms:
                    parts.append(_floor_score(b, float(prefs.min_bedrooms)))
                else:
                    # Over max: soft penalty
                    over = (b - prefs.max_bedrooms) / max(1.0, float(prefs.max_bedrooms))
                    parts.append(max(0.0, 1.0 - over))
            elif prefs.min_bedrooms is not None:
                parts.append(_floor_score(b, float(prefs.min_bedrooms)))
            else:
                # max-only
                mx = float(prefs.max_bedrooms)  # type: ignore[arg-type]
                if b <= mx:
                    parts.append(1.0)
                else:
                    over = (b - mx) / max(1.0, mx)
                    parts.append(max(0.0, 1.0 - over))

    if prefs.min_bathrooms is not None:
        baths = _listing_attr(listing, "bathrooms")
        if baths is not None:
            parts.append(_floor_score(float(baths), float(prefs.min_bathrooms)))

    if prefs.min_sqft is not None:
        sqft = _listing_attr(listing, "sqft")
        if sqft is not None:
            parts.append(_floor_score(float(sqft), float(prefs.min_sqft), soft_ratio=0.7))

    if prefs.require_den:
        den = match_amenity_feature(listing, AmenityFeature("den", "Den", ("den",)))
        if den.score is not None:
            parts.append(float(den.score))

    if prefs.house_categories:
        cat = (_listing_attr(listing, "house_category") or "").strip()
        if cat:
            parts.append(1.0 if _house_category_matches(cat, prefs.house_categories) else 0.0)

    # Only include structural if we had targets; if targets exist but no listing fields, omit
    has_targets = any(
        [
            prefs.budget_max is not None,
            prefs.min_bedrooms is not None,
            prefs.max_bedrooms is not None,
            prefs.min_bathrooms is not None,
            prefs.min_sqft is not None,
            bool(prefs.require_den),
            bool(prefs.house_categories),
        ]
    )
    if not has_targets:
        return None
    if not parts:
        return None
    return max(0.0, min(1.0, sum(parts) / len(parts)))


def score_proximity(
    listing: Union[dict, Any],
    proximity_rules: Optional[Sequence[dict]],
) -> Optional[float]:
    """Graded proximity: average clamp(1 - duration/max) over rules with known duration."""
    if not proximity_rules:
        return None
    scores: List[float] = []
    for rule in proximity_rules:
        if not isinstance(rule, dict):
            continue
        max_minutes = rule.get("max_minutes")
        if max_minutes is None:
            continue
        try:
            m = float(max_minutes)
        except (TypeError, ValueError):
            continue
        prox = _listing_attr(listing, "proximity")
        if not isinstance(prox, dict):
            continue
        key = _proximity_rule_key(rule)
        val = prox.get(key)
        if not isinstance(val, dict) or val.get("duration_min") is None:
            continue
        try:
            d = float(val["duration_min"])
        except (TypeError, ValueError):
            continue
        if m <= 0:
            scores.append(1.0 if d <= 0 else 0.0)
        elif d <= m:
            # At or under the cap is a full match (aligns with coverage checklist).
            scores.append(1.0)
        else:
            # Soft decay from 1 → 0 between max and 1.25× max.
            over = (d - m) / (m * 0.25)
            scores.append(max(0.0, 1.0 - over))
    if not scores:
        return None
    return sum(scores) / len(scores)


def score_amenity(
    listing: Union[dict, Any],
    amenity_features: Sequence[AmenityFeature],
    skip_ids: Optional[set[str]] = None,
) -> Optional[float]:
    """Average of known amenity matches; unknown excluded. Omit if no features.

    skip_ids: feature ids already counted elsewhere (e.g. den when require_den is
    scored in the structural component).
    """
    skip = skip_ids or set()
    if not amenity_features:
        return None
    known: List[float] = []
    for feat in amenity_features:
        if feat.id in skip:
            continue
        r = match_amenity_feature(listing, feat)
        if r.score is not None:
            known.append(float(r.score))
    if not known:
        return None
    return sum(known) / len(known)


def qualitative_for_semantic(prefs: EffectiveSearchPreferences) -> str:
    """Qualitative-only text for the semantic component (avoid re-embedding beds/price)."""
    parts: list[str] = []
    q = (prefs.qualitative_preferences or "").strip()
    if q:
        parts.append(q)
    if prefs.require_den and "den" not in q.lower():
        parts.append("den")
    return " ".join(parts).strip()


def listing_semantic_blob(listing: Union[dict, Any]) -> str:
    """Narrow listing blob for semantic scoring: description + amenities (+ den/parking hints)."""
    parts: list[str] = []
    for key in ("description", "ammenities", "nearby_ammenities", "title"):
        val = (_listing_attr(listing, key) or "").strip()
        if val:
            parts.append(val)
    has_den = _listing_attr(listing, "has_den")
    if has_den:
        parts.append("has den")
    spaces = _listing_attr(listing, "parking_spaces")
    ptype = (_listing_attr(listing, "parking_type") or "").strip()
    if spaces is not None:
        try:
            if int(spaces) >= 1:
                parts.append(f"{int(spaces)} parking")
        except (TypeError, ValueError):
            pass
    if ptype:
        parts.append(ptype)
    return " ".join(parts) if parts else " "
