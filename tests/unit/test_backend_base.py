"""Unit tests for the SearchBackend factory (backends/base.py)."""

import pytest

from rental_search_agent.backends.base import get_search_backend
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.backends.apify_realtor_ca import ApifyRealtorCaBackend


class TestGetSearchBackend:
    def test_defaults_to_ca_backend_when_unset(self, monkeypatch):
        monkeypatch.delenv("SEARCH_MARKET", raising=False)
        backend = get_search_backend()
        assert isinstance(backend, ApifyRealtorCaBackend)

    def test_ca_market_returns_ca_backend(self, monkeypatch):
        monkeypatch.setenv("SEARCH_MARKET", "ca")
        backend = get_search_backend()
        assert isinstance(backend, ApifyRealtorCaBackend)

    def test_unsupported_market_raises_search_backend_error(self, monkeypatch):
        # Must be SearchBackendError (not a raw ValueError): the rental_search call
        # site in client.py only catches SearchBackendError, so a misconfigured
        # SEARCH_MARKET should surface as a normal tool error, not an uncaught crash.
        monkeypatch.setenv("SEARCH_MARKET", "us")
        with pytest.raises(SearchBackendError, match="Unsupported SEARCH_MARKET"):
            get_search_backend()
