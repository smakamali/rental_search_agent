"""Google Geocoding API wrapper for resolving location strings to coordinates."""

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import List, Optional

from rental_search_agent.models import GeocodedReference, ProximityRule

logger = logging.getLogger(__name__)

# Skip geocoding for this location; resolved per-listing at enrichment time.
NEAREST_TRANSIT_LOCATION = "nearest transit station"

_GEOCODE_CACHE: dict[str, GeocodedReference] = {}
_GEOCODE_CACHE_MAX = 500


def _get_api_key() -> str:
    """Return Google Maps API key from environment."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "GOOGLE_MAPS_API_KEY is not set. Set it in .env for geocoding and proximity tools."
        )
    return key


def _normalize_location_key(location: str) -> str:
    """Normalize for cache key (lowercase, strip)."""
    return location.strip().lower()


def geocode_location(location: str) -> GeocodedReference:
    """Resolve a location string to coordinates using Google Geocoding API.

    Returns the first result. Caches results in memory.
    Raises ValueError if API key is missing or geocoding fails.
    """
    if not (location or location.strip()):
        raise ValueError("location must be a non-empty string.")
    key = _normalize_location_key(location)
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    api_key = _get_api_key()
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode(
        {"address": location.strip(), "key": api_key}
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("Geocoding request failed for %r: %s", location, e)
        raise ValueError(f"Geocoding failed for {location!r}: {e}") from e
    if data.get("status") != "OK":
        raise ValueError(
            f"Geocoding failed for {location!r}: {data.get('status', 'UNKNOWN')} - {data.get('error_message', '')}"
        )
    results = data.get("results") or []
    if not results:
        raise ValueError(f"No geocoding results for {location!r}.")
    first = results[0]
    geometry = first.get("geometry") or {}
    loc = geometry.get("location") or {}
    lat = loc.get("lat")
    lon = loc.get("lng")
    if lat is None or lon is None:
        raise ValueError(f"Missing lat/lng in geocode result for {location!r}.")
    display_name = first.get("formatted_address") or location.strip()
    ref = GeocodedReference(
        location=location.strip(),
        lat=float(lat),
        lon=float(lon),
        display_name=display_name,
    )
    if len(_GEOCODE_CACHE) < _GEOCODE_CACHE_MAX:
        _GEOCODE_CACHE[key] = ref
    return ref


def geocode_proximity_references(rules: List[ProximityRule]) -> List[GeocodedReference]:
    """Geocode all rule locations that are not 'nearest transit station'.

    Skips rules whose location is NEAREST_TRANSIT_LOCATION (resolved at enrichment time).
    Returns list of GeocodedReference in the same order as rules that require geocoding.
    """
    refs: List[GeocodedReference] = []
    for rule in rules:
        loc = (rule.location or "").strip()
        if not loc or _normalize_location_key(loc) == _normalize_location_key(NEAREST_TRANSIT_LOCATION):
            continue
        try:
            ref = geocode_location(loc)
            refs.append(ref)
        except ValueError as e:
            logger.warning("Skip geocoding for rule %r: %s", rule, e)
            # Optionally re-raise; plan says to geocode and cache. We skip failed ones.
            continue
    return refs
