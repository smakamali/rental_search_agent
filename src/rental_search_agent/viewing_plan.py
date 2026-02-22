"""Proximity-based viewing plan drafting. Clusters listings by location and assigns adjacent slots."""

import math
from typing import Any

from rental_search_agent.models import ViewingPlan, ViewingPlanEntry


def _slot_key(slot: dict[str, Any]) -> tuple[str, str]:
    """Return (start, end) tuple for slot identity."""
    return (str(slot.get("start", "")), str(slot.get("end", "")))


def _compute_unused_slots(
    entries: list[ViewingPlanEntry], available_slots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return slots from available_slots that are not assigned to any entry."""
    used = {(e.start_datetime, e.end_datetime) for e in entries}
    return [
        s
        for s in available_slots
        if (str(s.get("start", "")), str(s.get("end", ""))) not in used
    ]


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


def modify_viewing_plan(
    current_entries: list[ViewingPlanEntry | dict[str, Any]],
    available_slots: list[dict[str, Any]],
    *,
    remove: list[str] | None = None,
    add: list[dict[str, Any]] | None = None,
    update: list[dict[str, Any]] | None = None,
) -> ViewingPlan:
    """Modify a viewing plan: add, remove, or update entries.

    Args:
        current_entries: Existing plan entries (ViewingPlanEntry or dict).
        available_slots: Full list from calendar_get_available_slots.
        remove: Listing IDs to remove.
        add: List of {listing_id, listing_address, listing_url, slot: {start, end, display}}.
        update: List of {listing_id, new_slot: {start, end, display}}.

    Returns:
        ViewingPlan with modified entries.
    """
    remove = remove or []
    add = add or []
    update = update or []

    # Normalize to ViewingPlanEntry
    entries: list[ViewingPlanEntry] = []
    for e in current_entries:
        if isinstance(e, dict):
            entries.append(ViewingPlanEntry.model_validate(e))
        else:
            entries.append(e)

    available_keys = {_slot_key(s) for s in available_slots}
    used_keys: set[tuple[str, str]] = {(e.start_datetime, e.end_datetime) for e in entries}
    entry_by_id = {e.listing_id: e for e in entries}

    # Remove
    for lid in remove:
        if lid not in entry_by_id:
            raise ValueError(f"Listing {lid!r} not found in plan (cannot remove).")
        entry = entry_by_id.pop(lid)
        entries = [x for x in entries if x.listing_id != lid]
        used_keys.discard((entry.start_datetime, entry.end_datetime))

    # Update
    for item in update:
        lid = item.get("listing_id")
        new_slot = item.get("new_slot")
        if not lid or not new_slot:
            raise ValueError("update item must have listing_id and new_slot.")
        if lid not in entry_by_id:
            raise ValueError(f"Listing {lid!r} not found in plan (cannot update).")
        start = str(new_slot.get("start", ""))
        end = str(new_slot.get("end", ""))
        key = (start, end)
        if key not in available_keys:
            raise ValueError(f"Slot {start} - {end} is not in available_slots.")
        if key in used_keys:
            raise ValueError(f"Slot {start} - {end} is already used by another entry.")
        entry = entry_by_id[lid]
        used_keys.discard((entry.start_datetime, entry.end_datetime))
        display = str(new_slot.get("display", f"{start} - {end}"))
        updated = ViewingPlanEntry(
            listing_id=entry.listing_id,
            listing_address=entry.listing_address,
            listing_url=entry.listing_url,
            slot_display=display,
            start_datetime=start,
            end_datetime=end,
        )
        used_keys.add(key)
        entry_by_id[lid] = updated
        entries = [updated if x.listing_id == lid else x for x in entries]

    # Add
    for item in add:
        lid = str(item.get("listing_id", ""))
        address = str(item.get("listing_address", ""))
        url = str(item.get("listing_url", ""))
        slot = item.get("slot")
        if not lid or not address or not url or not slot:
            raise ValueError("add item must have listing_id, listing_address, listing_url, slot.")
        if lid in entry_by_id:
            raise ValueError(f"Listing {lid!r} is already in the plan (cannot add).")
        start = str(slot.get("start", ""))
        end = str(slot.get("end", ""))
        key = (start, end)
        if key not in available_keys:
            raise ValueError(f"Slot {start} - {end} is not in available_slots.")
        if key in used_keys:
            raise ValueError(f"Slot {start} - {end} is already used by another entry.")
        display = str(slot.get("display", f"{start} - {end}"))
        new_entry = ViewingPlanEntry(
            listing_id=lid,
            listing_address=address,
            listing_url=url,
            slot_display=display,
            start_datetime=start,
            end_datetime=end,
        )
        entries.append(new_entry)
        used_keys.add(key)
        entry_by_id[lid] = new_entry

    return ViewingPlan(entries=entries)
