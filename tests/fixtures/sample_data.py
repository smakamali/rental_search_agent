"""Reusable fixtures for rental_search_agent tests."""

import pandas as pd

from rental_search_agent.models import (
    Listing,
    ListingFilterCriteria,
    RentalSearchFilters,
)


def sample_listing(
    id: str = "mls-001",
    address: str = "123 Main St",
    price: float = 2800.0,
    price_display: str = "$2,800/month",
    bedrooms: int = 2,
    bathrooms: float | None = 2.0,
    sqft: float | None = 1000.0,
    **kwargs,
) -> Listing:
    """Create a sample Listing with sensible defaults."""
    defaults = {
        "id": id,
        "title": f"Listing {id}",
        "url": f"https://www.realtor.ca/listing/{id}",
        "address": address,
        "price": price,
        "price_display": price_display,
        "bedrooms": bedrooms,
        "sqft": sqft,
        "source": "Realtor.ca",
        "bathrooms": bathrooms,
    }
    defaults.update(kwargs)
    return Listing(**defaults)


def sample_listings(n: int = 3) -> list[Listing]:
    """Create n sample listings with varied data."""
    return [
        sample_listing(id=f"mls-{i:03d}", address=f"{100 + i} Main St", price=2500 + i * 200)
        for i in range(1, n + 1)
    ]


def sample_rental_filters(
    min_bedrooms: int = 2,
    location: str = "Vancouver",
    **kwargs,
) -> RentalSearchFilters:
    """Create sample RentalSearchFilters."""
    return RentalSearchFilters(min_bedrooms=min_bedrooms, location=location, **kwargs)


def sample_filter_criteria(**kwargs) -> ListingFilterCriteria:
    """Create sample ListingFilterCriteria (all optional)."""
    return ListingFilterCriteria(**{k: v for k, v in kwargs.items() if v is not None})


def mock_pyRealtor_row(
    mls: str = "mls-001",
    address: str = "123 Main St",
    bedrooms: int = 2,
    bathrooms: float | None = 2.0,
    size: str | float | None = "1000 sqft",
    rent: float | str = 2800,
    total_rent: float | None = None,
    website: str | None = "https://www.realtor.ca/listing/mls-001",
    description: str = "Nice apartment",
    **kwargs,
) -> pd.Series:
    """Create a mock pandas Series mimicking pyRealtor output row."""
    data = {
        "MLS": mls,
        "Address": address,
        "Bedrooms": bedrooms,
        "Bathrooms": bathrooms,
        "Size": size,
        "Rent": rent,
        "Website": website,
        "Description": description,
        "Postal Code": "V6B 1A1",
        "Latitude": 49.28,
        "Longitude": -123.12,
        "House Category": "Apartment",
        "Ownership Category": "Condominium",
        "Ammenities": "Balcony",
        "Nearby Ammenities": "",
        "Open House": "",
        "Stories": 1,
    }
    if total_rent is not None:
        data["Total Rent"] = total_rent
    data.update(kwargs)
    return pd.Series(data)
