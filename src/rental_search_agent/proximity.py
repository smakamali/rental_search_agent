"""Proximity enrichment: nearest transit station (Places API) and distance/duration (Distance Matrix / Directions API)."""

import concurrent.futures
import json
import logging
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from rental_search_agent.geocoding import NEAREST_TRANSIT_LOCATION
from rental_search_agent.models import GeocodedReference, Listing, ProximityRule

logger = logging.getLogger(__name__)

# Cache: (lat_rounded, lon_rounded) -> (lat, lon, name)
_PLACES_CACHE: Dict[Tuple[float, float], Tuple[float, float, str]] = {}
_PLACES_CACHE_MAX = 300
# Cache: (origin_key, dest_key, mode) -> (distance_km, duration_min)
_DIRECTIONS_CACHE: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
_DIRECTIONS_CACHE_MAX = 2000
_ROUND = 4  # round coords to 4 decimals for cache key (~11m precision)
_MATRIX_BATCH_SIZE = 25  # Distance Matrix API limit per request


def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise ValueError("GOOGLE_MAPS_API_KEY is not set. Set it in .env for proximity tools.")
    return key


def _round_coord(x: float) -> float:
    return round(x, _ROUND)


def _cache_key(olat: float, olon: float, dlat: float, dlon: float, mode: str) -> Tuple[str, str, str]:
    ok = (_round_coord(olat), _round_coord(olon))
    dk = (_round_coord(dlat), _round_coord(dlon))
    return (str(ok), str(dk), mode)


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
    """Get distance (km) and duration (minutes) via Directions API. Used for per-listing transit-station paths."""
    ck = _cache_key(origin_lat, origin_lon, dest_lat, dest_lon, mode)
    if ck in _DIRECTIONS_CACHE:
        return _DIRECTIONS_CACHE[ck]
    api_key = _get_api_key()
    url = "https://maps.googleapis.com/maps/api/directions/json?" + urllib.parse.urlencode(
        {
            "origin": f"{origin_lat},{origin_lon}",
            "destination": f"{dest_lat},{dest_lon}",
            "mode": mode,
            "key": api_key,
        }
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
    result = (float(dist_m) / 1000.0, float(dur_s) / 60.0)
    if len(_DIRECTIONS_CACHE) < _DIRECTIONS_CACHE_MAX:
        _DIRECTIONS_CACHE[ck] = result
    return result


def _get_distance_matrix_batch(
    origins: List[Tuple[float, float]],
    destinations: List[Tuple[float, float]],
    mode: str,
) -> List[List[Optional[Tuple[float, float]]]]:
    """Call Distance Matrix API for a batch of origins and destinations (up to 25 each).

    Returns results[i][j] = (distance_km, duration_min) or None for origin i -> destination j.
    Populates _DIRECTIONS_CACHE for each successfully resolved cell.
    Cells already in cache are returned directly without an API call for those origins.
    """
    n_orig = len(origins)
    n_dest = len(destinations)
    results: List[List[Optional[Tuple[float, float]]]] = [[None] * n_dest for _ in range(n_orig)]

    # Populate from cache and identify which origins still need a fetch
    origins_to_fetch: List[Tuple[float, float]] = []
    fetch_idx_map: List[int] = []  # fetch_idx_map[k] = original origin index
    for i, (olat, olon) in enumerate(origins):
        needs_fetch = False
        for j, (dlat, dlon) in enumerate(destinations):
            ck = _cache_key(olat, olon, dlat, dlon, mode)
            if ck in _DIRECTIONS_CACHE:
                results[i][j] = _DIRECTIONS_CACHE[ck]
            else:
                needs_fetch = True
        if needs_fetch:
            origins_to_fetch.append((olat, olon))
            fetch_idx_map.append(i)

    if not origins_to_fetch:
        return results

    api_key = _get_api_key()
    origins_str = "|".join(f"{lat},{lon}" for lat, lon in origins_to_fetch)
    destinations_str = "|".join(f"{dlat},{dlon}" for dlat, dlon in destinations)
    url = "https://maps.googleapis.com/maps/api/distancematrix/json?" + urllib.parse.urlencode(
        {
            "origins": origins_str,
            "destinations": destinations_str,
            "mode": mode,
            "key": api_key,
        }
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("Distance Matrix request failed: %s", e)
        return results

    if data.get("status") != "OK":
        logger.warning("Distance Matrix API status: %s", data.get("status"))
        return results

    rows = data.get("rows") or []
    for fetch_i, row in enumerate(rows):
        if fetch_i >= len(fetch_idx_map):
            break
        orig_i = fetch_idx_map[fetch_i]
        olat, olon = origins[orig_i]
        elements = row.get("elements") or []
        for j, element in enumerate(elements):
            if j >= n_dest:
                break
            dlat, dlon = destinations[j]
            if element.get("status") != "OK":
                continue
            dist_m = (element.get("distance") or {}).get("value")
            dur_s = (element.get("duration") or {}).get("value")
            if dist_m is None or dur_s is None:
                continue
            cell = (float(dist_m) / 1000.0, float(dur_s) / 60.0)
            results[orig_i][j] = cell
            ck = _cache_key(olat, olon, dlat, dlon, mode)
            if len(_DIRECTIONS_CACHE) < _DIRECTIONS_CACHE_MAX:
                _DIRECTIONS_CACHE[ck] = cell

    return results


def _rule_key(rule: ProximityRule) -> str:
    """Stable key for a rule (for listing.proximity dict)."""
    return f"{rule.location}|{rule.mode}"


def _normalize_mode(mode: Optional[str]) -> str:
    """Convert ProximityRule.mode to Google API mode string."""
    if mode == "transit":
        return "transit"
    if mode == "walk":
        return "walking"
    return "driving"


def _enrich_single_listing_transit(
    listing: Listing,
    transit_rules: List[ProximityRule],
) -> Dict[str, Any]:
    """Enrich one listing for nearest-transit-station rules (Places + Directions, called in a thread)."""
    prox: Dict[str, Any] = {}
    lat = listing.latitude
    lon = listing.longitude
    if lat is None or lon is None:
        for rule in transit_rules:
            prox[_rule_key(rule)] = None
        return prox
    station = get_nearest_transit_station(float(lat), float(lon))
    if not station:
        for rule in transit_rules:
            prox[_rule_key(rule)] = None
        return prox
    dest_lat, dest_lon, _ = station
    for rule in transit_rules:
        rk = _rule_key(rule)
        mode_api = _normalize_mode(rule.mode)
        result = _get_directions(float(lat), float(lon), dest_lat, dest_lon, mode_api)
        if result is None:
            prox[rk] = None
        else:
            distance_km, duration_min = result
            prox[rk] = {"distance_km": round(distance_km, 2), "duration_min": round(duration_min, 1)}
    return prox


def enrich_listings_with_proximity(
    listings: List[Any],
    rules: List[ProximityRule],
    geocoded_refs: List[GeocodedReference],
) -> List[Dict[str, Any]]:
    """Enrich each listing with proximity data (distance_km, duration_min) per rule.

    Fixed-destination rules (all except 'nearest transit station') are resolved via the
    Distance Matrix API, batched 25 origins at a time per mode -- one API call per batch
    instead of one per listing. Nearest-transit-station rules are resolved in parallel
    using a ThreadPoolExecutor (Places + Directions per listing, up to 5 concurrent workers).

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

    n = len(listing_objs)
    prox_data: List[Dict[str, Any]] = [{} for _ in range(n)]

    # Separate rules into fixed-destination vs. nearest-transit-station
    transit_station_rules: List[ProximityRule] = []
    fixed_dest_rules: List[ProximityRule] = []
    for rule in rules:
        if (rule.location or "").strip().lower() == NEAREST_TRANSIT_LOCATION.lower():
            transit_station_rules.append(rule)
        else:
            fixed_dest_rules.append(rule)

    # ------------------------------------------------------------------
    # Fixed-destination rules: Distance Matrix API (batched by mode)
    # ------------------------------------------------------------------
    if fixed_dest_rules:
        # Resolve each rule to its destination coords; mark unresolvable rules as None
        rule_to_dest: Dict[str, Tuple[float, float]] = {}
        for rule in fixed_dest_rules:
            rk = _rule_key(rule)
            ref = refs_by_location.get((rule.location or "").strip())
            if ref:
                rule_to_dest[rk] = (ref.lat, ref.lon)
            else:
                for i in range(n):
                    prox_data[i][rk] = None

        # Group resolvable rules by API mode
        mode_to_rules: Dict[str, List[ProximityRule]] = defaultdict(list)
        for rule in fixed_dest_rules:
            rk = _rule_key(rule)
            if rk in rule_to_dest:
                mode_to_rules[_normalize_mode(rule.mode)].append(rule)

        # Listings with valid coordinates
        coord_listings: List[Tuple[int, float, float]] = [
            (i, float(lst.latitude), float(lst.longitude))
            for i, lst in enumerate(listing_objs)
            if lst.latitude is not None and lst.longitude is not None
        ]
        coord_set = {i for i, _, _ in coord_listings}

        for mode_api, mode_rules in mode_to_rules.items():
            destinations = [rule_to_dest[_rule_key(r)] for r in mode_rules]

            # Mark listings without coordinates
            for i in range(n):
                if i not in coord_set:
                    for rule in mode_rules:
                        prox_data[i][_rule_key(rule)] = None

            # Batch origins in chunks of _MATRIX_BATCH_SIZE
            for batch_start in range(0, len(coord_listings), _MATRIX_BATCH_SIZE):
                orig_batch = coord_listings[batch_start : batch_start + _MATRIX_BATCH_SIZE]
                orig_indices = [t[0] for t in orig_batch]
                origins = [(t[1], t[2]) for t in orig_batch]

                # Batch destinations in chunks of _MATRIX_BATCH_SIZE (rarely more than one chunk)
                for dest_start in range(0, len(destinations), _MATRIX_BATCH_SIZE):
                    dest_batch = destinations[dest_start : dest_start + _MATRIX_BATCH_SIZE]
                    rules_batch = mode_rules[dest_start : dest_start + _MATRIX_BATCH_SIZE]

                    matrix = _get_distance_matrix_batch(origins, dest_batch, mode_api)

                    for batch_i, orig_i in enumerate(orig_indices):
                        row = matrix[batch_i] if batch_i < len(matrix) else []
                        for j, rule in enumerate(rules_batch):
                            rk = _rule_key(rule)
                            cell = row[j] if j < len(row) else None
                            if cell is None:
                                prox_data[orig_i][rk] = None
                            else:
                                distance_km, duration_min = cell
                                prox_data[orig_i][rk] = {
                                    "distance_km": round(distance_km, 2),
                                    "duration_min": round(duration_min, 1),
                                }

    # ------------------------------------------------------------------
    # Nearest-transit-station rules: parallelized per listing
    # ------------------------------------------------------------------
    if transit_station_rules:
        # Mark listings without coordinates upfront
        listings_to_enrich = [
            i for i, lst in enumerate(listing_objs)
            if lst.latitude is not None and lst.longitude is not None
        ]
        for i, lst in enumerate(listing_objs):
            if lst.latitude is None or lst.longitude is None:
                for rule in transit_station_rules:
                    prox_data[i][_rule_key(rule)] = None

        if listings_to_enrich:
            def _enrich_idx(i: int) -> Tuple[int, Dict[str, Any]]:
                return (i, _enrich_single_listing_transit(listing_objs[i], transit_station_rules))

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(_enrich_idx, i): i for i in listings_to_enrich}
                for future in concurrent.futures.as_completed(futures):
                    orig_i = futures[future]
                    try:
                        _, result = future.result()
                        prox_data[orig_i].update(result)
                    except Exception as e:
                        logger.warning("Transit enrichment failed for listing index %d: %s", orig_i, e)
                        for rule in transit_station_rules:
                            prox_data[orig_i].setdefault(_rule_key(rule), None)

    # Assemble output
    out: List[Dict[str, Any]] = []
    for i, listing in enumerate(listing_objs):
        listing_dict = listing.model_dump()
        listing_dict["proximity"] = prox_data[i]
        out.append(listing_dict)
    return out
