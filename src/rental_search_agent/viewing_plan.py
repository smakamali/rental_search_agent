"""Proximity-based viewing plan drafting. Clusters listings by location and assigns adjacent slots."""

import math
from typing import Any

from rental_search_agent.models import ViewingPlan, ViewingPlanEntry


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two points (haversine formula)."""
    R = 6371  # Earth radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _cluster_by_proximity(
    listings: list[dict[str, Any]], threshold_km: float = 2.0
) -> list[list[dict[str, Any]]]:
    """Cluster listings by proximity. Listings without lat/long form a final cluster."""
    with_coords: list[dict[str, Any]] = []
    without_coords: list[dict[str, Any]] = []
    for lst in listings:
        lat = lst.get("latitude")
        lon = lst.get("longitude")
        if lat is not None and lon is not None:
            try:
                float(lat)
                float(lon)
                with_coords.append(lst)
            except (TypeError, ValueError):
                without_coords.append(lst)
        else:
            without_coords.append(lst)

    if not with_coords:
        return [without_coords] if without_coords else []

    clusters: list[list[dict[str, Any]]] = []
    used: set[int] = set()
    items = [(i, lst) for i, lst in enumerate(with_coords)]

    for idx, lst in items:
        if idx in used:
            continue
        cluster = [lst]
        used.add(idx)
        lat1 = float(lst.get("latitude", 0))
        lon1 = float(lst.get("longitude", 0))
        for jdx, other in items:
            if jdx in used:
                continue
            lat2 = float(other.get("latitude", 0))
            lon2 = float(other.get("longitude", 0))
            if _haversine_km(lat1, lon1, lat2, lon2) <= threshold_km:
                cluster.append(other)
                used.add(jdx)
        clusters.append(cluster)

    # Sort clusters by centroid latitude (north-to-south)
    def centroid(c: list[dict]) -> float:
        lats = [float(x.get("latitude", 0)) for x in c if x.get("latitude") is not None]
        return sum(lats) / len(lats) if lats else 0

    clusters.sort(key=centroid, reverse=True)

    # Within each cluster, sort by (lat, lon)
    for cluster in clusters:
        cluster.sort(
            key=lambda x: (
                float(x.get("latitude") or 0),
                float(x.get("longitude") or 0),
            )
        )

    if without_coords:
        clusters.append(without_coords)
    return clusters


def draft_viewing_plan(
    listings: list[dict[str, Any]], available_slots: list[dict[str, Any]]
) -> ViewingPlan:
    """Draft a viewing plan: assign available slots to listings, clustering nearby listings.

    Args:
        listings: Selected listings with id, address, url, latitude, longitude.
        available_slots: From calendar tool, each {start, end, display}.

    Returns:
        ViewingPlan with entries. Raises ValueError if more listings than slots.
    """
    if not listings:
        return ViewingPlan(entries=[])
    if not available_slots:
        raise ValueError("No available slots. Get more slots or reduce the number of listings.")
    if len(listings) > len(available_slots):
        raise ValueError(
            f"Not enough slots: {len(listings)} listings but only {len(available_slots)} slots. "
            "Expand the date range or reduce the number of listings."
        )

    clusters = _cluster_by_proximity(listings)
    slot_idx = 0
    entries: list[ViewingPlanEntry] = []
    for cluster in clusters:
        for lst in cluster:
            slot = available_slots[slot_idx]
            slot_idx += 1
            start = slot.get("start", "")
            end = slot.get("end", "")
            display = slot.get("display", f"{start} - {end}")
            entries.append(
                ViewingPlanEntry(
                    listing_id=str(lst.get("id", "")),
                    listing_address=str(lst.get("address", "")),
                    listing_url=str(lst.get("url", "")),
                    slot_display=display,
                    start_datetime=start,
                    end_datetime=end,
                )
            )
    return ViewingPlan(entries=entries)
