"""Pytest configuration and shared fixtures."""

import pytest

from tests.fixtures.sample_data import (
    mock_pyRealtor_row,
    sample_filter_criteria,
    sample_listing,
    sample_listings,
    sample_rental_filters,
)


@pytest.fixture
def listing():
    """Single sample listing."""
    return sample_listing()


@pytest.fixture
def listings():
    """List of 3 sample listings."""
    return sample_listings(3)


@pytest.fixture
def rental_filters():
    """Sample rental search filters."""
    return sample_rental_filters()


@pytest.fixture
def filter_criteria():
    """Sample filter criteria (empty, all optional)."""
    return sample_filter_criteria()


@pytest.fixture
def pyrealtor_row():
    """Mock pyRealtor DataFrame row."""
    return mock_pyRealtor_row()
