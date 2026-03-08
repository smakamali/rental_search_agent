"""Proximity enrichment: nearest transit station (Places API) and distance/duration (Directions API)."""

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from rental_search_agent.geocoding import NEAREST_TRANSIT_LOCATION
from rental_search_agent.models import GeocodedReference, Listing, ProximityRule

logger = logging.getLogger(__name__)

# Cache: (lat_rounded, lon_rounded, "transit") -> (lat, lon, name)
_PLACES_CACHE: Dict[Tuple[float, float], Tuple[float, float, str]] = {}
_PLACES_CACHE_MAX = 300
# Cache: (origin_key, dest_key, mode) -> (distance_km, duration_min)
_DIRECTIONS_CACHE: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
_DIRECTIONS_CACHE_MAX = 2000
_ROUND = 4  # round coords to 4 decimals for cache key (~11m precision)


def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise ValueError("GOOGLE_MAPS_API_KEY is not set. Set it in .env for proximity tools.")
    return key


def _round_coord(x: float) -> float:
    return round(x, _ROUND)


def get_nearest_transit_station(lat: float, lon: float) -> Optional[Tuple[float, float, str]]:
    """Find the nearest transit station to (lat, lon) using Google Places Nearby Search.

    Returns (station_lat, station_lon, station_name) or None if not found.
    """
    key_cache = (_round_coord(lat), _round_coord(lon))
    if key_cache in _PLACES_CACHE:
        return _PLACES_CACHE[key_cache]
    api_key = _get_api_key()
    location = f"{lat},{lon}"
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json?" + urllib.parse.urlencode(
        {"location": location, "type": "transit_station", "rankby": "distance", "key": api_key}
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("Places nearby search failed: %s", e)
        return None
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        logger.warning("Places API status: %s", data.get("status"))
        return None
    results = data.get("results") or []
    if not results:
        return None
    first = results[0]
    geometry = first.get("geometry") or {}
    loc = geometry.get("location") or {}
    station_lat = loc.get("lat")
    station_lon = loc.get("lng")
    name = first.get("name") or "Transit station"
    if station_lat is None or station_lon is None:
        return None
    result = (float(station_lat), float(station_lon), name)
    if len(_PLACES_CACHE) < _PLACES_CACHE_MAX:
        _PLACES_CACHE[key_cache] = result
    return result


def _get_directions(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, mode: str
) -> Optional[Tuple[float, float]]:
    """Get distance (km) and duration (minutes) from origin to destination. mode: driving, walking, transit."""
    ok = (_round_coord(origin_lat), _round_coord(origin_lon))
    dk = (_round_coord(dest_lat), _round_coord(dest_lon))
    cache_key = (str(ok), str(dk), mode)
    if cache_key in _DIRECTIONS_CACHE:
        return _DIRECTIONS_CACHE[cache_key]
    api_key = _get_api_key()
    origin = f"{origin_lat},{origin_lon}"
    destination = f"{dest_lat},{dest_lon}"
    url = "https://maps.googleapis.com/maps/api/directions/json?" + urllib.parse.urlencode(
        {"origin": origin, "destination": destination, "mode": mode, "key": api_key}
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("Directions request failed: %s", e)
        return None
    if data.get("status") != "OK":
        return None
    routes = data.get("routes") or []
    if not routes:
        return None
    legs = routes[0].get("legs") or []
    if not legs:
        return None
    leg = legs[0]
    dist_m = leg.get("distance", {}).get("value")
    dur_s = leg.get("duration", {}).get("value")
    if dist_m is None or dur_s is None:
        return None
    distance_km = float(dist_m) / 1000.0
    duration_min = float(dur_s) / 60.0
    result = (distance_km, duration_min)
    if len(_DIRECTIONS_CACHE) < _DIRECTIONS_CACHE_MAX:
        _DIRECTIONS_CACHE[cache_key] = result
    return result


def _rule_key(rule: ProximityRule) -> str:
    """Stable key for a rule (for listing.proximity dict)."""
    return f"{rule.location}|{rule.mode}"


def enrich_listings_with_proximity(
    listings: List[Any],
    rules: List[ProximityRule],
    geocoded_refs: List[GeocodedReference],
) -> List[Dict[str, Any]]:
    """Enrich each listing with proximity data (distance_km, duration_min) per rule.

    listings: list of Listing or listing dicts.
    geocoded_refs: refs for non–'nearest transit station' rules; matched to rules by ref.location == rule.location.
    For rules with location 'nearest transit station', the destination is resolved per listing via get_nearest_transit_station.
    Listings without lat/lon get proximity[rule_key] = None (unknown).
    Returns list of listing dicts with 'proximity' key set.
    """
    refs_by_location: Dict[str, GeocodedReference] = {r.location.strip(): r for r in geocoded_refs}
    listing_objs: List[Listing] = []
    for item in listings:
        if isinstance(item, dict):
            listing_objs.append(Listing.model_validate(item))
        else:
            listing_objs.append(item)
    out: List[Dict[str, Any]] = []
    for listing in listing_objs:
        prox: Dict[str, Any] = {}
        lat = listing.latitude
        lon = listing.longitude
        has_coords = lat is not None and lon is not None
        for rule in rules:
            rk = _rule_key(rule)
            if not has_coords:
                prox[rk] = None
                continue
            dest_lat: Optional[float] = None
            dest_lon: Optional[float] = None
            if (rule.location or "").strip().lower() == NEAREST_TRANSIT_LOCATION.lower():
                station = get_nearest_transit_station(float(lat), float(lon))
                if not station:
                    prox[rk] = None
                    continue
                dest_lat, dest_lon, _ = station
            else:
                ref = refs_by_location.get((rule.location or "").strip())
                if not ref:
                    prox[rk] = None
                    continue
                dest_lat, dest_lon = ref.lat, ref.lon
            mode = rule.mode or "drive"
            if mode == "transit":
                mode = "transit"
            elif mode == "walk":
                mode = "walking"
            else:
                mode = "driving"
            result = _get_directions(float(lat), float(lon), dest_lat, dest_lon, mode)
            if result is None:
                prox[rk] = None
                continue
            distance_km, duration_min = result
            prox[rk] = {"distance_km": round(distance_km, 2), "duration_min": round(duration_min, 1)}
            time.sleep(0.05)  # slight throttle to avoid rate limits
        listing_dict = listing.model_dump()
        listing_dict["proximity"] = prox
        out.append(listing_dict)
    return out
