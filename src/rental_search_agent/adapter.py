"""Property search adapter: Apify Realtor.ca backend → Listing shape.

Single-city searches go straight to the backend. Multi-city searches fan out
in parallel here, then merge/dedupe by listing id so the rest of the pipeline
still sees one master list.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from rental_search_agent.backends.base import get_search_backend
from rental_search_agent.search_regions import canonicalize_search_locations
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.models import (
    FailedSearchLocation,
    Listing,
    RentalSearchFilters,
    RentalSearchResponse,
)

__all__ = ["SearchBackendError", "merge_listings_by_id", "search"]

logger = logging.getLogger(__name__)

# Cap concurrent Apify actor runs so a 20-city selection does not stampede the API.
_FANOUT_MAX_WORKERS = 8


def merge_listings_by_id(listings: list[Listing]) -> list[Listing]:
    """Concatenate listings, keeping the first occurrence of each Listing.id."""
    seen: set[str] = set()
    out: list[Listing] = []
    for listing in listings:
        lid = (listing.id or "").strip()
        if lid:
            if lid in seen:
                continue
            seen.add(lid)
        out.append(listing)
    return out


def _search_one_city(backend: Any, filters: RentalSearchFilters, city: str) -> RentalSearchResponse:
    city_filters = filters.model_copy(update={"location": city})
    return backend.search(city_filters)


def _user_facing_search_error(exc: BaseException) -> str:
    if isinstance(exc, SearchBackendError):
        return str(exc)
    return "The rental search is temporarily unavailable."


def search(filters: RentalSearchFilters, use_proxy: bool = False) -> RentalSearchResponse:
    """
    Run a single logical search via the configured backend (Canada Apify).

    ``location`` may be one city or a list of cities. Multi-city runs scrape in
    parallel and return one merged list. ``use_proxy`` is accepted for call-site
    compatibility and ignored (Apify manages proxies). On total backend failure,
    raises SearchBackendError (never a silent empty list).
    """
    _ = use_proxy  # unused; Apify handles anti-bot / proxies
    backend = get_search_backend()
    locations = canonicalize_search_locations(filters.location_list())
    if not locations:
        raise SearchBackendError("location must contain at least one non-empty city name.")
    if len(locations) == 1:
        resp = _search_one_city(backend, filters, locations[0])
        return RentalSearchResponse(
            listings=resp.listings,
            total_count=len(resp.listings),
            searched_locations=locations,
            failed_locations=[],
        )

    workers = min(_FANOUT_MAX_WORKERS, len(locations))
    results: dict[str, RentalSearchResponse | BaseException] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_city = {
            pool.submit(_search_one_city, backend, filters, city): city for city in locations
        }
        for future in as_completed(future_to_city):
            city = future_to_city[future]
            try:
                results[city] = future.result()
            except Exception as exc:
                results[city] = exc

    searched: list[str] = []
    failed: list[FailedSearchLocation] = []
    merged: list[Listing] = []
    for city in locations:
        result = results[city]
        if isinstance(result, BaseException):
            err = _user_facing_search_error(result)
            failed.append(FailedSearchLocation(location=city, error=err))
            logger.warning("Search failed for %s: %s", city, result)
            continue
        searched.append(city)
        merged.extend(result.listings)

    if not searched:
        cities = ", ".join(f.location for f in failed) or ", ".join(locations)
        raise SearchBackendError(
            "The rental search is temporarily unavailable for all selected cities "
            f"({cities})."
        )

    merged = merge_listings_by_id(merged)
    return RentalSearchResponse(
        listings=merged,
        total_count=len(merged),
        searched_locations=searched,
        failed_locations=failed,
    )
