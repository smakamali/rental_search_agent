"""Search backends. Canada Apify is the current implementation; US can plug in later."""

from rental_search_agent.backends.base import SearchBackend, get_search_backend
from rental_search_agent.backends.errors import SearchBackendError

__all__ = ["SearchBackend", "SearchBackendError", "get_search_backend"]
