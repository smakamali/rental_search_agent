"""Property search adapter: Apify Realtor.ca backend → Listing shape.

Single-city searches go straight to the backend. Multi-city searches fan out
in parallel here, then merge/dedupe by listing id so the rest of the pipeline
still sees one master list.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from rental_search_agent.backends.base import get_search_backend
from rental_search_agent.search_regions import canonicalize_search_locations
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.logging_config import format_run_id_suffix, log_stage
from rental_search_agent.models import (
    FailedSearchLocation,
    Listing,
    RentalSearchFilters,
    RentalSearchResponse,
)

__all__ = ["SearchBackendError", "get_fanout_max_workers", "merge_listings_by_id", "search"]

logger = logging.getLogger(__name__)

# Cap concurrent Apify actor runs so a large city selection does not stampede the API.
# Free Apify plans allow 5 concurrent runs; override via APIFY_MAX_CONCURRENT.
_DEFAULT_FANOUT_MAX_WORKERS = 5


def get_fanout_max_workers() -> int:
    """Max concurrent multi-city Apify actor runs (APIFY_MAX_CONCURRENT, default 5)."""
    raw = (os.environ.get("APIFY_MAX_CONCURRENT") or "").strip()
    if not raw:
        return _DEFAULT_FANOUT_MAX_WORKERS
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_FANOUT_MAX_WORKERS
    return max(1, n)


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

    provider = type(backend).__name__
    n_locations = len(locations)
    workers = 1 if n_locations == 1 else min(get_fanout_max_workers(), n_locations)

    with log_stage(
        logger,
        "adapter.search",
        n_locations=n_locations,
        workers=workers,
        provider=provider,
    ):
        if n_locations == 1:
            resp = _search_one_city(backend, filters, locations[0])
            out = RentalSearchResponse(
                listings=resp.listings,
                total_count=len(resp.listings),
                searched_locations=locations,
                failed_locations=[],
            )
            logger.info(
                "adapter.search done searched=1 failed=0 listings=%d%s",
                len(out.listings),
                format_run_id_suffix(),
            )
            if not out.listings:
                logger.warning(
                    "adapter.search returned zero listings location=%s%s",
                    locations[0],
                    format_run_id_suffix(),
                )
            return out

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
                logger.warning(
                    "Search failed for %s: %s%s",
                    city,
                    result,
                    format_run_id_suffix(),
                )
                continue
            searched.append(city)
            merged.extend(result.listings)

        if not searched:
            cities = ", ".join(f.location for f in failed) or ", ".join(locations)
            raise SearchBackendError(
                "The rental search is temporarily unavailable for all selected cities "
                f"({cities})."
            )

        before_dedupe = len(merged)
        merged = merge_listings_by_id(merged)
        deduped = before_dedupe - len(merged)
        out = RentalSearchResponse(
            listings=merged,
            total_count=len(merged),
            searched_locations=searched,
            failed_locations=failed,
        )
        logger.info(
            "adapter.search done searched=%d failed=%d listings=%d "
            "before_dedupe=%d deduped=%d%s",
            len(searched),
            len(failed),
            len(merged),
            before_dedupe,
            deduped,
            format_run_id_suffix(),
        )
        if not merged:
            logger.warning(
                "adapter.search returned zero listings searched=%d failed=%d%s",
                len(searched),
                len(failed),
                format_run_id_suffix(),
            )
        return out
