"""Reusable fixtures for rental_search_agent tests."""

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


def sample_available_slots(n: int = 3) -> list[dict]:
    """Create n sample available slot dicts with start, end, display."""
    from datetime import datetime, timedelta

    base = datetime(2026, 2, 25, 18, 0, 0)
    slots = []
    for i in range(n):
        start = base + timedelta(hours=i * 2)
        end = start + timedelta(hours=1)
        slots.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "display": start.strftime("%A %b %d, %I:%M%p"),
        })
    return slots


def sample_listings_with_coords() -> list[dict]:
    """Listings for clustering tests: two downtown Vancouver (close), one Surrey (far)."""
    return [
        {
            "id": "mls-001",
            "address": "123 Main St, Vancouver",
            "url": "https://example.com/1",
            "latitude": 49.28,
            "longitude": -123.12,
        },
        {
            "id": "mls-002",
            "address": "456 Granville St, Vancouver",
            "url": "https://example.com/2",
            "latitude": 49.283,
            "longitude": -123.115,
        },
        {
            "id": "mls-003",
            "address": "789 King George Blvd, Surrey",
            "url": "https://example.com/3",
            "latitude": 49.19,
            "longitude": -122.85,
        },
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


def mock_apify_item(
    mls: str = "mls-001",
    address: str = "123 Main St",
    bedrooms: int = 2,
    bathrooms: float | None = 2.0,
    size: str | float | None = "1000 sqft",
    price: float = 2800,
    price_display: str | None = None,
    relative_url: str | None = None,
    description: str = "Nice apartment",
    postal: str = "V6B 1A1",
    lat: float | None = 49.28,
    lon: float | None = -123.12,
    building_type: str = "Apartment",
    ownership: str = "Condominium",
    parking_spaces: int | str | None = None,
    parking_type: str | None = None,
    **kwargs,
) -> dict:
    """Create a mock igolaizola / Realtor.ca-style dataset item."""
    if relative_url is None:
        relative_url = f"/real-estate/123/{mls}"
    if price_display is None:
        price_display = f"${int(price):,}"
    property_obj: dict = {
        "Price": price_display,
        "PriceUnformattedValue": price,
        "Address": {
            "AddressText": address,
            "PostalCode": postal,
            "Latitude": lat,
            "Longitude": lon,
        },
    }
    if parking_spaces is not None:
        property_obj["ParkingSpaceTotal"] = parking_spaces
    if parking_type is not None:
        property_obj["ParkingType"] = parking_type
    item = {
        "Id": mls,
        "MlsNumber": mls,
        "RelativeURLEn": relative_url,
        "PublicRemarks": description,
        "OwnershipType": ownership,
        "Property": property_obj,
        "Building": {
            "Bedrooms": bedrooms,
            "BathroomTotal": bathrooms,
            "SizeInterior": size,
            "Type": building_type,
            "Stories": 1,
        },
    }
    item.update(kwargs)
    return item
