"""In-memory filter and sort for search results. Used by filter_listings tool."""

import logging
import re
from collections import Counter
from typing import Any, List, Optional

from rental_search_agent.models import Listing, ListingFilterCriteria, ProximityRule, RentalSearchResponse

logger = logging.getLogger(__name__)

# Attributes that can be used for sorting
SORTABLE_ATTRS = frozenset(
    {
        "price",
        "bedrooms",
        "bathrooms",
        "sqft",
        "address",
        "id",
        "title",
        "semantic_score",
        "match_score",
        "proximity",
        "listing_age_hours",
    }
)

# Map common user/Realtor.ca variants onto a small set of canonical keys for OR matching.
_HOUSE_CATEGORY_ALIASES: dict[str, str] = {
    "apartment": "apartment",
    "apartments": "apartment",
    "condo": "apartment",
    "condos": "apartment",
    "condominium": "apartment",
    "condominiums": "apartment",
    "house": "house",
    "houses": "house",
    "single family": "house",
    "detached": "house",
    "row townhouse": "row townhouse",
    "row townhouses": "row townhouse",
    "townhouse": "row townhouse",
    "townhouses": "row townhouse",
    "town house": "row townhouse",
    "town houses": "row townhouse",
    "row": "row townhouse",
    "rowhouse": "row townhouse",
    "rowhouses": "row townhouse",
    "row house": "row townhouse",
    "row houses": "row townhouse",
    "duplex": "duplex",
    "duplexes": "duplex",
    "triplex": "triplex",
    "triplexes": "triplex",
    "fourplex": "fourplex",
    "fourplexes": "fourplex",
    "mobile home": "mobile home",
    "mobile homes": "mobile home",
    "manufactured home": "mobile home",
    "manufactured homes": "mobile home",
}


def _normalize_house_category(value: str) -> str:
    """Lowercase and collapse separators so 'Row / Townhouse' and 'row-townhouse' compare equal."""
    return re.sub(r"[\s/_\-]+", " ", (value or "").strip().lower()).strip()


def _canonical_house_category(value: str) -> str:
    """Return alias-canonical form when known; otherwise the normalized string.

    Also tries stripping a trailing 's' from the last token so filter values like
    'Apartments' match listing 'Apartment', and 'Townhouses' match 'Row / Townhouse'.
    """
    normalized = _normalize_house_category(value)
    if normalized in _HOUSE_CATEGORY_ALIASES:
        return _HOUSE_CATEGORY_ALIASES[normalized]
    parts = normalized.split()
    if parts and parts[-1].endswith("s") and len(parts[-1]) > 3:
        singularized = " ".join(parts[:-1] + [parts[-1][:-1]])
        if singularized in _HOUSE_CATEGORY_ALIASES:
            return _HOUSE_CATEGORY_ALIASES[singularized]
        return singularized
    return normalized


def _house_category_matches(listing_category: str | None, allowed: list[str]) -> bool:
    """True if listing_category matches any allowed entry (case-insensitive + aliases)."""
    if not allowed:
        return True
    if listing_category is None or not str(listing_category).strip():
        return False
    listing_key = _canonical_house_category(str(listing_category))
    allowed_keys = {_canonical_house_category(a) for a in allowed if a and str(a).strip()}
    if not allowed_keys:
        return True
    return listing_key in allowed_keys


def _min_proximity_minutes(listing: Listing | dict) -> float:
    """Return the minimum duration_min across all proximity rules for this listing.
    Returns inf if no proximity data is present (sorts to end when ascending)."""
    if isinstance(listing, dict):
        prox = listing.get("proximity")
    else:
        prox = getattr(listing, "proximity", None)
    if not isinstance(prox, dict):
        return float("inf")
    minutes = []
    for val in prox.values():
        if isinstance(val, dict) and val.get("duration_min") is not None:
            try:
                minutes.append(float(val["duration_min"]))
            except (TypeError, ValueError):
                pass
    return min(minutes) if minutes else float("inf")


def _get_sort_key(listing: Listing | dict, attr: str) -> Any:
    """Extract sort key from listing. None/missing values sort to end."""
    if attr == "proximity":
        minutes = _min_proximity_minutes(listing)
        if minutes == float("inf"):
            return (1, float("inf"))
        return (0, minutes)
    if isinstance(listing, dict):
        val = listing.get(attr)
    else:
        val = getattr(listing, attr, None)
    if val is None:
        if attr in ("price", "bedrooms", "bathrooms", "sqft", "semantic_score", "match_score", "listing_age_hours"):
            return (
                1,
                float("-inf") if attr in ("semantic_score", "match_score") else float("inf"),
            )
        return (1, "")
    if attr in ("price", "bedrooms", "bathrooms", "sqft", "semantic_score", "match_score", "listing_age_hours"):
        return (0, float(val))
    return (0, str(val))


def _listing_field_values(listing: Listing | dict) -> tuple[Any, Any, Any, Any, Any]:
    if isinstance(listing, dict):
        return (
            listing.get("bedrooms"),
            listing.get("bathrooms"),
            listing.get("sqft"),
            listing.get("price"),
            listing.get("house_category"),
        )
    return (
        listing.bedrooms,
        listing.bathrooms,
        listing.sqft,
        listing.price,
        listing.house_category,
    )


def _structural_drop_reason(listing: Listing | dict, criteria: ListingFilterCriteria) -> Optional[str]:
    """Return first structural drop-reason key, or None if listing passes criteria."""
    bedrooms, bathrooms, sqft, price, house_category = _listing_field_values(listing)

    if criteria.min_bedrooms is not None:
        if bedrooms is None:
            return "missing_fields"
        if bedrooms < criteria.min_bedrooms:
            return "beds"
    if criteria.max_bedrooms is not None:
        if bedrooms is None:
            return "missing_fields"
        if bedrooms > criteria.max_bedrooms:
            return "beds"
    if criteria.min_bathrooms is not None:
        if bathrooms is None:
            return "missing_fields"
        if bathrooms < criteria.min_bathrooms:
            return "baths"
    if criteria.max_bathrooms is not None:
        if bathrooms is None:
            return "missing_fields"
        if bathrooms > criteria.max_bathrooms:
            return "baths"
    if criteria.min_sqft is not None:
        if sqft is None:
            return "missing_fields"
        if sqft < criteria.min_sqft:
            return "sqft"
    if criteria.max_sqft is not None:
        if sqft is None:
            return "missing_fields"
        if sqft > criteria.max_sqft:
            return "sqft"
    if criteria.price_min is not None:
        if price is None:
            return "missing_fields"
        if price < criteria.price_min:
            return "price"
    if criteria.price_max is not None:
        if price is None:
            return "missing_fields"
        if price > criteria.price_max:
            return "price"
    if criteria.house_categories:
        if house_category is None or not str(house_category).strip():
            return "missing_fields"
        if not _house_category_matches(house_category, criteria.house_categories):
            return "category"
    return None


def _listing_matches(listing: Listing | dict, criteria: ListingFilterCriteria) -> bool:
    """Return True if listing satisfies all non-None criteria."""
    return _structural_drop_reason(listing, criteria) is None


def _rule_key(rule: ProximityRule) -> str:
    """Stable key for a rule (must match proximity.py)."""
    return f"{rule.location}|{rule.mode}"


def _listing_matches_proximity(listing: Listing | dict, rules: List[ProximityRule]) -> bool:
    """Return True if listing satisfies all proximity rules (AND). Unknown proximity for a rule keeps the listing."""
    if not rules:
        return True
    if isinstance(listing, dict):
        prox = listing.get("proximity")
    else:
        prox = getattr(listing, "proximity", None)
    if not isinstance(prox, dict):
        return True  # no proximity data: keep (treat as unknown)
    for rule in rules:
        rk = _rule_key(rule)
        val = prox.get(rk)
        if val is None:
            continue  # unknown: keep
        if not isinstance(val, dict):
            continue
        duration_min = val.get("duration_min")
        if duration_min is None:
            continue
        try:
            if float(duration_min) > rule.max_minutes:
                return False
        except (TypeError, ValueError):
            continue
    return True


def filter_listings(
    listings: list[Listing] | list[dict],
    criteria: ListingFilterCriteria | dict,
    *,
    sort_by: Optional[str] = None,
    ascending: bool = True,
    proximity_rules: Optional[List[ProximityRule]] = None,
) -> RentalSearchResponse:
    """Filter in-memory listings by criteria and/or proximity rules (AND). Optionally sort. Returns same shape as rental_search. Listings with unknown proximity for a rule are kept."""
    if isinstance(criteria, dict):
        criteria = ListingFilterCriteria.model_validate(criteria)
    n_in = len(listings)
    drop_counts: Counter[str] = Counter()
    filtered: list[Listing] = []
    for item in listings:
        if isinstance(item, dict):
            listing = Listing.model_validate(item)
        else:
            listing = item
        reason = _structural_drop_reason(listing, criteria)
        if reason is not None:
            drop_counts[reason] += 1
            continue
        if proximity_rules and not _listing_matches_proximity(listing, proximity_rules):
            drop_counts["proximity"] += 1
            continue
        filtered.append(listing)
    if sort_by and sort_by in SORTABLE_ATTRS:
        filtered.sort(key=lambda lst: _get_sort_key(lst, sort_by), reverse=not ascending)
    n_out = len(filtered)
    drops = dict(drop_counts)
    if n_in > 0 and n_out == 0:
        logger.warning(
            "filter_listings: wiped all listings n_in=%d n_out=0 drops=%s",
            n_in,
            drops,
        )
    else:
        logger.debug(
            "filter_listings: n_in=%d n_out=%d drops=%s",
            n_in,
            n_out,
            drops,
        )
    return RentalSearchResponse(listings=filtered, total_count=n_out)
