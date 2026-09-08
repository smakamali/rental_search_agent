"""Deterministic post-search pipeline: structural filter → proximity → score.

Used after every successful rental_search and from the sidebar Search button.
The LLM does not own this path.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from rental_search_agent.filtering import filter_listings
from rental_search_agent.geocoding import geocode_proximity_references
from rental_search_agent.match_scoring import score_listings_by_preferences
from rental_search_agent.models import ListingFilterCriteria, ProximityRule
from rental_search_agent.preference_resolution import (
    EffectiveSearchPreferences,
    effective_to_filter_fields,
    merge_chat_over_stored,
    stored_prefs_to_effective,
)
from rental_search_agent.proximity import enrich_listings_with_proximity
from rental_search_agent.proximity_parser import parse_proximity_preferences
from rental_search_agent.search_regions import resolve_search_location_input

logger = logging.getLogger(__name__)

APPLY_TOOL_NAME = "apply_search_preferences"

# Sidebar fields overlaid onto last-search filters (not location / listing_type).
STRUCTURAL_SCRAPE_KEYS = (
    "budget_max",
    "min_bedrooms",
    "max_bedrooms",
    "min_bathrooms",
    "min_sqft",
)

# Sidebar fields that, if changed, cannot be applied in-memory (need a new scrape).
SCRAPE_PREF_KEYS = STRUCTURAL_SCRAPE_KEYS + ("location", "listing_type")

ProgressCallback = Callable[[str, str, bool], None]


@dataclass
class ApplyPreferencesResult:
    """Outcome of apply_search_preferences."""

    listings: list[dict]
    proximity_rules: list[dict] = field(default_factory=list)
    last_sort_by: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    display_source: Optional[str] = None
    applied: bool = False

    def to_tool_payload(self) -> dict[str, Any]:
        return {
            "listings": self.listings,
            "total_count": len(self.listings),
            "warnings": self.warnings,
            "rules": self.proximity_rules,
            "last_sort_by": self.last_sort_by,
            "skipped": self.skipped,
            "display_source": self.display_source,
        }


def with_display_rank(listings: list[dict]) -> list[dict]:
    """Attach an explicit 1-based rank matching current array order."""
    out: list[dict] = []
    for i, listing in enumerate(listings):
        d = dict(listing) if isinstance(listing, dict) else listing
        d["rank"] = i + 1
        out.append(d)
    return out


def structural_filter_criteria(prefs: EffectiveSearchPreferences) -> dict[str, Any]:
    """Map effective prefs to filter_listings fields (no location/listing_type)."""
    fields = effective_to_filter_fields(prefs)
    fields.pop("location", None)
    fields.pop("listing_type", None)
    return fields


def pipeline_needed(prefs: EffectiveSearchPreferences) -> bool:
    """True when the pipeline would filter, enrich, or score."""
    if structural_filter_criteria(prefs):
        return True
    if (prefs.proximity_preferences or "").strip():
        return True
    return prefs.has_score_relevant_prefs()


def _pref_text(prefs: Mapping[str, Any] | None, key: str) -> str:
    return str((prefs or {}).get(key) or "").strip()


def scrape_prefs_changed(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> bool:
    """True when a scrape-relevant sidebar field changed (hard constraints)."""
    for key in SCRAPE_PREF_KEYS:
        if _pref_text(before, key) != _pref_text(after, key):
            return True
    return False


def structural_prefs_changed(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> bool:
    """True when a scrape-relevant sidebar field changed."""
    return scrape_prefs_changed(before, after)


def _normalize_listing_type(value: Any) -> Optional[str]:
    s = str(value or "").strip().lower()
    if s in ("for_rent", "rent", "rental"):
        return "for_rent"
    if s in ("for_sale", "sale", "buy"):
        return "for_sale"
    return None


def filters_from_sidebar_prefs(prefs: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build rental_search filters entirely from the sidebar form."""
    prefs = prefs or {}
    effective = stored_prefs_to_effective(prefs)
    fields = effective_to_filter_fields(effective)
    resolved = resolve_search_location_input(_pref_text(prefs, "location"))
    if resolved:
        fields["location"] = resolved
    fields["listing_type"] = _normalize_listing_type(prefs.get("listing_type")) or "for_rent"
    return fields


def first_search_required_warnings(prefs: Mapping[str, Any] | None) -> list[str]:
    """Validation errors when scraping with no prior master list."""
    warnings: list[str] = []
    if not _pref_text(prefs, "location"):
        warnings.append("Enter a location to search.")
    if not _pref_text(prefs, "min_bedrooms"):
        warnings.append("Enter a minimum bedroom count to search.")
    return warnings


def _scrape_spinner(filters: Mapping[str, Any] | None) -> str:
    loc = (filters or {}).get("location")
    n = len(loc) if isinstance(loc, list) else 1
    if n > 1:
        return f"Searching {n} cities..."
    return "Searching..."


@dataclass
class SidebarSearchRequest:
    """Pure scrape / re-rank decision for the sidebar Search button."""

    kind: str
    warnings: list[str] = field(default_factory=list)
    filters: Optional[dict[str, Any]] = None
    spinner: str = ""


def prepare_sidebar_search(
    new_prefs: Mapping[str, Any],
    previous_prefs: Mapping[str, Any] | None,
    *,
    has_master: bool,
    last_filters: Mapping[str, Any] | None,
) -> SidebarSearchRequest:
    """Decide scrape vs re-rank and build filters when a scrape is needed."""
    if has_master and not scrape_prefs_changed(previous_prefs, new_prefs):
        return SidebarSearchRequest(kind="rerank", spinner="Updating rankings...")

    if not has_master or not last_filters:
        warnings = first_search_required_warnings(new_prefs)
        if warnings:
            return SidebarSearchRequest(kind="error", warnings=warnings)
        filters = filters_from_sidebar_prefs(new_prefs)
        return SidebarSearchRequest(
            kind="scrape",
            filters=filters,
            spinner=_scrape_spinner(filters),
        )

    filters = overlay_structural_on_search_filters(
        last_filters, new_prefs, previous_prefs=previous_prefs
    )
    return SidebarSearchRequest(
        kind="scrape",
        filters=filters,
        spinner=_scrape_spinner(filters),
    )


def overlay_structural_on_search_filters(
    last_filters: Mapping[str, Any],
    prefs: Mapping[str, Any],
    *,
    previous_prefs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild rental_search filters: last location/listing_type plus new structural prefs.

    When ``previous_prefs`` is provided (sidebar Search), only scrape keys that changed
    are overlaid, so a budget edit does not drop last-search ``max_bedrooms`` / ``price_min``.
    A location change is resolved (metro → full city list); listing_type is taken from
    the form. Keys the form never collects (``max_bathrooms``, ``max_sqft``, ``price_min``)
    are kept from last search unless the overlay explicitly sets them.
    """
    out = dict(last_filters or {})
    effective = stored_prefs_to_effective(prefs)
    fields = effective_to_filter_fields(effective)

    if previous_prefs is None:
        keys_to_apply = STRUCTURAL_SCRAPE_KEYS
    else:
        keys_to_apply = tuple(
            key
            for key in STRUCTURAL_SCRAPE_KEYS
            if _pref_text(previous_prefs, key) != _pref_text(prefs, key)
        )

    pref_to_filter = {
        "budget_max": "price_max",
        "min_bedrooms": "min_bedrooms",
        "max_bedrooms": "max_bedrooms",
        "min_bathrooms": "min_bathrooms",
        "min_sqft": "min_sqft",
    }
    for pref_key in keys_to_apply:
        filter_key = pref_to_filter[pref_key]
        if filter_key in fields:
            out[filter_key] = fields[filter_key]
        else:
            out.pop(filter_key, None)

    loc_changed = previous_prefs is None or _pref_text(previous_prefs, "location") != _pref_text(
        prefs, "location"
    )
    if loc_changed:
        resolved = resolve_search_location_input(_pref_text(prefs, "location"))
        if resolved:
            out["location"] = resolved

    type_changed = previous_prefs is None or (
        _pref_text(previous_prefs, "listing_type") != _pref_text(prefs, "listing_type")
    )
    if type_changed:
        listing_type = _normalize_listing_type(prefs.get("listing_type"))
        if listing_type:
            out["listing_type"] = listing_type

    if "min_bedrooms" not in out or out.get("min_bedrooms") is None:
        out["min_bedrooms"] = last_filters.get("min_bedrooms", 0)

    if not out.get("listing_type"):
        out["listing_type"] = last_filters.get("listing_type") or "for_rent"
    return out


def structural_prefs_for_rerank(
    saved_prefs: Mapping[str, Any] | None,
    search_criteria: Mapping[str, Any] | None,
) -> EffectiveSearchPreferences:
    """Structural filter for Path A Apply: unchanged stored prefs, chat search wins."""
    return merge_chat_over_stored(saved_prefs, search_criteria)


def make_apply_tool_messages(result: ApplyPreferencesResult) -> list[dict]:
    """Synthetic assistant+tool pair so history recovers the pipeline output."""
    call_id = f"apply-{uuid.uuid4().hex[:12]}"
    arguments = json.dumps(
        {
            "last_sort_by": result.last_sort_by,
            "display_source": result.display_source,
        }
    )
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": APPLY_TOOL_NAME,
                        "arguments": arguments,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(result.to_tool_payload()),
        },
    ]


def make_rental_search_tool_messages(
    filters: Mapping[str, Any],
    result_data: Mapping[str, Any],
) -> list[dict]:
    """Synthetic rental_search exchange so sidebar Search updates conversation master."""
    call_id = f"search-{uuid.uuid4().hex[:12]}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "rental_search",
                        "arguments": json.dumps({"filters": dict(filters)}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(dict(result_data)),
        },
    ]


def _to_dicts(listings: Sequence[Any]) -> list[dict]:
    out: list[dict] = []
    for item in listings:
        if isinstance(item, dict):
            out.append(dict(item))
        elif hasattr(item, "model_dump"):
            out.append(item.model_dump())
        else:
            out.append(dict(item))
    return out


def _emit(progress: ProgressCallback | None, name: str, phase: str, ok: bool = True) -> None:
    if progress is None:
        return
    try:
        progress(name, phase, ok)
    except Exception:
        logger.debug("preference_apply progress callback failed", exc_info=True)


def apply_search_preferences(
    listings: Sequence[Any],
    prefs: EffectiveSearchPreferences,
    *,
    structural_prefs: Optional[EffectiveSearchPreferences] = None,
    progress: ProgressCallback | None = None,
) -> ApplyPreferencesResult:
    """Filter, optionally enrich with proximity, and score listings.

    ``structural_prefs`` defaults to ``prefs``. Path A Apply uses last-search
    structural criteria here while scoring with the just-saved form prefs.
    """
    current = _to_dicts(listings)
    warnings: list[str] = []
    skipped: list[str] = []
    last_sort_by: Optional[str] = None
    display_source: Optional[str] = None
    applied = False
    proximity_rules: list[ProximityRule] = []
    proximity_rule_dicts: list[dict] = []

    struct_src = structural_prefs if structural_prefs is not None else prefs
    criteria_dict = structural_filter_criteria(struct_src)
    if criteria_dict:
        try:
            resp = filter_listings(current, criteria_dict)
            current = _to_dicts(resp.listings)
            applied = True
            display_source = "filter"
        except Exception as e:
            logger.warning("Structural filter in apply pipeline failed: %s", e)
            warnings.append(f"Could not apply structural filters: {e}")
            skipped.append("structural")
    else:
        skipped.append("structural")

    proximity_text = (prefs.proximity_preferences or "").strip()
    if proximity_text:
        _emit(progress, "enrich_listings_with_proximity", "start")
        prox_ok = False
        try:
            proximity_rules = list(parse_proximity_preferences(proximity_text) or [])
            if not proximity_rules:
                warnings.append("No proximity rules could be parsed from preferences.")
                skipped.append("proximity")
            else:
                refs = geocode_proximity_references(proximity_rules)
                current = enrich_listings_with_proximity(current, proximity_rules, refs)
                resp = filter_listings(
                    current,
                    ListingFilterCriteria(),
                    sort_by="proximity",
                    ascending=True,
                    proximity_rules=proximity_rules,
                )
                current = _to_dicts(resp.listings)
                proximity_rule_dicts = [r.model_dump() for r in proximity_rules]
                last_sort_by = "proximity"
                display_source = "enrich"
                applied = True
                prox_ok = True
        except ValueError as e:
            logger.warning("Proximity step skipped: %s", e)
            warnings.append(str(e))
            skipped.append("proximity")
        except Exception as e:
            logger.warning("Proximity step failed: %s", e)
            warnings.append(f"Could not compute proximity: {e}")
            skipped.append("proximity")
        _emit(progress, "enrich_listings_with_proximity", "end", prox_ok)
    else:
        skipped.append("proximity")

    if prefs.has_score_relevant_prefs() or proximity_rule_dicts:
        _emit(progress, "score_listings_by_preferences", "start")
        score_ok = False
        try:
            current = score_listings_by_preferences(
                current,
                preferences_text=prefs.qualitative_preferences or "",
                effective_prefs=prefs,
                proximity_rules=proximity_rule_dicts,
            )
            last_sort_by = "match_score"
            display_source = "score"
            applied = True
            score_ok = True
        except Exception as e:
            logger.warning("Scoring step failed: %s", e)
            warnings.append(f"Could not score listings: {e}")
            skipped.append("score")
        _emit(progress, "score_listings_by_preferences", "end", score_ok)
    else:
        skipped.append("score")

    current = with_display_rank(current)
    return ApplyPreferencesResult(
        listings=current,
        proximity_rules=proximity_rule_dicts,
        last_sort_by=last_sort_by,
        warnings=warnings,
        skipped=skipped,
        display_source=display_source,
        applied=applied,
    )
