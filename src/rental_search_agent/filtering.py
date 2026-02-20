"""In-memory filter and sort for search results. Used by filter_listings tool."""

from typing import Any, Optional

from rental_search_agent.models import Listing, ListingFilterCriteria, RentalSearchResponse

# Attributes that can be used for sorting
SORTABLE_ATTRS = frozenset({"price", "bedrooms", "bathrooms", "sqft", "address", "id", "title"})


def _get_sort_key(listing: Listing | dict, attr: str) -> Any:
    """Extract sort key from listing. None/missing values sort to end."""
    if isinstance(listing, dict):
        val = listing.get(attr)
    else:
        val = getattr(listing, attr, None)
    if val is None:
        if attr in ("price", "bedrooms", "bathrooms", "sqft"):
            return (1, float("inf"))
        return (1, "")
    if attr in ("price", "bedrooms", "bathrooms", "sqft"):
        return (0, float(val))
    return (0, str(val))


def _listing_matches(listing: Listing | dict, criteria: ListingFilterCriteria) -> bool:
    """Return True if listing satisfies all non-None criteria."""
    if isinstance(listing, dict):
        bedrooms = listing.get("bedrooms")
        bathrooms = listing.get("bathrooms")
        sqft = listing.get("sqft")
        price = listing.get("price")
    else:
        bedrooms = listing.bedrooms
        bathrooms = listing.bathrooms
        sqft = listing.sqft
        price = listing.price

    if criteria.min_bedrooms is not None:
        if bedrooms is None or bedrooms < criteria.min_bedrooms:
            return False
    if criteria.max_bedrooms is not None:
        if bedrooms is None or bedrooms > criteria.max_bedrooms:
            return False
    if criteria.min_bathrooms is not None:
        if bathrooms is None or bathrooms < criteria.min_bathrooms:
            return False
    if criteria.max_bathrooms is not None:
        if bathrooms is None or bathrooms > criteria.max_bathrooms:
            return False
    if criteria.min_sqft is not None:
        if sqft is None or sqft < criteria.min_sqft:
            return False
    if criteria.max_sqft is not None:
        if sqft is None or sqft > criteria.max_sqft:
            return False
    if criteria.rent_min is not None:
        if price is None or price < criteria.rent_min:
            return False
    if criteria.rent_max is not None:
        if price is None or price > criteria.rent_max:
            return False
    return True


def filter_listings(
    listings: list[Listing] | list[dict],
    criteria: ListingFilterCriteria | dict,
    *,
    sort_by: Optional[str] = None,
    ascending: bool = True,
) -> RentalSearchResponse:
    """Filter in-memory listings by criteria. Optionally sort by attribute. Returns same shape as rental_search."""
    if isinstance(criteria, dict):
        criteria = ListingFilterCriteria.model_validate(criteria)
    filtered: list[Listing] = []
    for item in listings:
        if isinstance(item, dict):
            listing = Listing.model_validate(item)
        else:
            listing = item
        if _listing_matches(listing, criteria):
            filtered.append(listing)
    if sort_by and sort_by in SORTABLE_ATTRS:
        filtered.sort(key=lambda lst: _get_sort_key(lst, sort_by), reverse=not ascending)
    return RentalSearchResponse(listings=filtered, total_count=len(filtered))
