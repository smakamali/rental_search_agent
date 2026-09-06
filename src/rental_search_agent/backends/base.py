"""Search backend Protocol and factory. SEARCH_MARKET=ca today; us reserved for later."""

from __future__ import annotations

import os
from typing import Protocol

from rental_search_agent.models import RentalSearchFilters, RentalSearchResponse


class SearchBackend(Protocol):
    """Pluggable listing search backend (Canada now; US later)."""

    def search(self, filters: RentalSearchFilters) -> RentalSearchResponse:
        """Run a search and return mapped listings. Raises SearchBackendError on failure."""
        ...


def get_search_backend() -> SearchBackend:
    """Return the configured search backend.

    SEARCH_MARKET defaults to \"ca\". A future \"us\" value will select a US actor backend.
    """
    market = (os.environ.get("SEARCH_MARKET") or "ca").strip().lower()
    if market in ("", "ca", "canada"):
        from rental_search_agent.backends.apify_realtor_ca import ApifyRealtorCaBackend

        return ApifyRealtorCaBackend()
    # Raise SearchBackendError (not a raw ValueError) so a misconfigured SEARCH_MARKET
    # surfaces as a normal tool error to the LLM/user instead of an uncaught exception —
    # the rental_search call site in client.py only catches SearchBackendError.
    from rental_search_agent.backends.errors import SearchBackendError

    raise SearchBackendError(
        f"Unsupported SEARCH_MARKET={market!r}. Only 'ca' is implemented; US support is planned."
    )
