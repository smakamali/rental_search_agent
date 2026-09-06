"""Property search adapter: Apify Realtor.ca backend → Listing shape."""

from rental_search_agent.backends.base import get_search_backend
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.models import RentalSearchFilters, RentalSearchResponse

__all__ = ["SearchBackendError", "search"]


def search(filters: RentalSearchFilters, use_proxy: bool = False) -> RentalSearchResponse:
    """
    Run a single logical search via the configured backend (Canada Apify).

    ``use_proxy`` is accepted for call-site compatibility and ignored (Apify
    manages proxies). On backend failure, raises SearchBackendError.
    """
    _ = use_proxy  # unused; Apify handles anti-bot / proxies
    return get_search_backend().search(filters)
