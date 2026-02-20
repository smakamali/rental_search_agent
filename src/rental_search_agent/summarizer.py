"""Compute structured statistics from search results. Used by summarize_listings tool."""

import statistics
from typing import Any

from rental_search_agent.models import Listing


def _get(listing: Listing | dict, attr: str) -> Any:
    """Extract attribute from listing (dict or Listing)."""
    if isinstance(listing, dict):
        return listing.get(attr)
    return getattr(listing, attr, None)


def summarize_listings(listings: list[Listing] | list[dict]) -> dict:
    """Compute statistics for listings. Returns structured dict for summary."""
    if not listings:
        return {
            "count": 0,
            "price": None,
            "bedrooms": {"distribution": {}},
            "bathrooms": {"distribution": {}, "count_with_data": 0, "min": None, "median": None, "max": None},
            "sqft": None,
            "house_category": {},
        }

    prices = [float(_get(l, "price")) for l in listings if _get(l, "price") is not None]
    bedrooms_list = [_get(l, "bedrooms") for l in listings if _get(l, "bedrooms") is not None]
    bathrooms_list = [_get(l, "bathrooms") for l in listings if _get(l, "bathrooms") is not None]
    sqft_list = [float(_get(l, "sqft")) for l in listings if _get(l, "sqft") is not None]
    house_cats = [_get(l, "house_category") for l in listings if _get(l, "house_category")]

    result: dict[str, Any] = {
        "count": len(listings),
    }

    # Price
    if prices:
        result["price"] = {
            "min": round(min(prices)),
            "median": round(statistics.median(prices)),
            "mean": round(statistics.mean(prices)),
            "max": round(max(prices)),
        }
    else:
        result["price"] = None

    # Bedrooms distribution (keys as string for JSON)
    bed_dist: dict[str, int] = {}
    for b in bedrooms_list:
        k = str(int(b)) if b is not None else "0"
        bed_dist[k] = bed_dist.get(k, 0) + 1
    result["bedrooms"] = {"distribution": dict(sorted(bed_dist.items(), key=lambda x: float(x[0])))}

    # Bathrooms: distribution + min/median/max
    bath_dist: dict[str, int] = {}
    for b in bathrooms_list:
        if b is not None:
            k = str(int(b)) if b == int(b) else str(b)
            bath_dist[k] = bath_dist.get(k, 0) + 1
    bath_with_data = [float(b) for b in bathrooms_list if b is not None]
    result["bathrooms"] = {
        "distribution": dict(sorted(bath_dist.items(), key=lambda x: float(x[0]))),
        "count_with_data": len(bath_with_data),
        "min": round(min(bath_with_data), 1) if bath_with_data else None,
        "median": round(statistics.median(bath_with_data), 1) if bath_with_data else None,
        "max": round(max(bath_with_data), 1) if bath_with_data else None,
    }

    # Sqft
    if sqft_list:
        result["sqft"] = {
            "count_with_data": len(sqft_list),
            "min": round(min(sqft_list)),
            "median": round(statistics.median(sqft_list)),
            "max": round(max(sqft_list)),
        }
    else:
        result["sqft"] = None

    # House category (omit empty)
    cat_counts: dict[str, int] = {}
    for c in house_cats:
        if c and str(c).strip():
            s = str(c).strip()
            cat_counts[s] = cat_counts.get(s, 0) + 1
    result["house_category"] = dict(sorted(cat_counts.items(), key=lambda x: -x[1]))

    return result
