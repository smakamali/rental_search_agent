"""Shared search backend errors."""


class SearchBackendError(Exception):
    """Raised when the property search backend fails (timeout, network error, etc.)."""

    # Backends should raise this rather than silently returning an empty result list.
    pass
