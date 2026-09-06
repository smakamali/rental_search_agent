"""Backend-agnostic helpers shared by all SearchBackend implementations.

These operate purely on RentalSearchFilters/Listing and raw scalar values — no
Realtor.ca- or Apify-specific logic — so a future backend (e.g. a US market
backend) can reuse them instead of duplicating or reaching into a sibling
backend module.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from rental_search_agent.models import Listing, RentalSearchFilters


def _format_price_display(raw: Any, price: float, listing_type: str) -> Optional[str]:
    """Prefer a formatted source string when present; else format from numeric price."""
    if raw is not None and not (isinstance(raw, float) and raw != raw):  # NaN
        s = str(raw).strip()
        if s and ("$" in s or "," in s):
            return s
    if not price:
        return None
    if listing_type == "for_rent":
        return f"${int(price):,}/month"
    return f"${int(price):,}"


# Realtor.ca listings report interior size in either sqft or square metres depending
# on the listing/region (e.g. "723 sqft" vs "65.0316 m2" for otherwise-similar condos).
_SQM_TO_SQFT = 10.7639
_METRIC_UNIT_RE = re.compile(r"m\s*2|m\u00b2|sq\s*\.?\s*m\b|sqm\b", re.IGNORECASE)


def _parse_sqft(val: Any) -> Optional[float]:
    """Parse interior size to float sqft, converting square metres to sqft when the
    source string indicates metric units (e.g. '65.0316 m2' -> ~700 sqft)."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val != val:  # NaN
            return None
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    match = re.search(r"[\d.]+", s)
    if not match:
        return None
    number = float(match.group())
    if _METRIC_UNIT_RE.search(s):
        return round(number * _SQM_TO_SQFT, 1)
    return number


def _coerce_float(val: Any) -> Optional[float]:
    """Parse a numeric value that may be a string with formatting cruft (currency
    symbols, thousands separators, unit suffixes) or a negative number as a string
    (e.g. longitude '-123.116411' from the Apify actor). Preserves a leading '-' so
    coordinates aren't silently flipped to the wrong hemisphere.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val != val:  # NaN
            return None
        return float(val)
    raw = str(val).strip()
    negative = raw.startswith("-")
    s = re.sub(r"[^\d.]", "", raw)
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if negative else f


def _coerce_int(val: Any, default: int = 0) -> int:
    f = _coerce_float(val)
    if f is None:
        return default
    return int(f)


def post_filter_listings(
    listings: list[Listing],
    filters: RentalSearchFilters,
) -> list[Listing]:
    """Safety-net filter for criteria a backend may not have enforced server-side."""
    out: list[Listing] = []
    for listing in listings:
        if filters.min_bedrooms is not None and listing.bedrooms < filters.min_bedrooms:
            continue
        if filters.max_bedrooms is not None and listing.bedrooms > filters.max_bedrooms:
            continue
        if filters.min_bathrooms is not None:
            if listing.bathrooms is None or listing.bathrooms < filters.min_bathrooms:
                continue
        if filters.max_bathrooms is not None:
            if listing.bathrooms is None or listing.bathrooms > filters.max_bathrooms:
                continue
        if filters.min_sqft is not None:
            if listing.sqft is None or listing.sqft < filters.min_sqft:
                continue
        if filters.max_sqft is not None:
            if listing.sqft is None or listing.sqft > filters.max_sqft:
                continue
        if filters.price_min is not None and listing.price < filters.price_min:
            continue
        if filters.price_max is not None and listing.price > filters.price_max:
            continue
        out.append(listing)
    return out
