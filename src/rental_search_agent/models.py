"""Data models per technical spec §4 and §5."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# (min_field, max_field) pairs that must satisfy min <= max when both are set.
# Shared by RentalSearchFilters and ListingFilterCriteria, which mirror these bounds.
_MIN_MAX_FIELD_PAIRS = (
    ("min_bedrooms", "max_bedrooms"),
    ("min_bathrooms", "max_bathrooms"),
    ("min_sqft", "max_sqft"),
    ("price_min", "price_max"),
)


def _check_min_max_pairs(model: BaseModel) -> None:
    """Raise ValueError if any min bound exceeds its paired max bound.

    Without this, an inverted range (e.g. price_min > price_max) silently produces
    zero results with no explanation, since both bounds get pushed to the backend
    and/or the post-filter as valid-looking but contradictory constraints.
    """
    for min_field, max_field in _MIN_MAX_FIELD_PAIRS:
        min_val = getattr(model, min_field, None)
        max_val = getattr(model, max_field, None)
        if min_val is not None and max_val is not None and min_val > max_val:
            raise ValueError(f"{min_field} ({min_val}) must not exceed {max_field} ({max_val}).")


class RentalSearchFilters(BaseModel):
    """§4.1 Search filters (input to rental_search). Supports for_rent and for_sale."""

    min_bedrooms: int = Field(..., ge=0, description="Minimum number of bedrooms.")
    max_bedrooms: Optional[int] = Field(None, ge=0, description="Maximum number of bedrooms.")
    min_bathrooms: Optional[int] = Field(None, ge=0, description="Minimum number of bathrooms.")
    max_bathrooms: Optional[int] = Field(None, ge=0, description="Maximum number of bathrooms.")
    min_sqft: Optional[int] = Field(None, ge=0, description="Minimum square footage.")
    max_sqft: Optional[int] = Field(None, ge=0, description="Maximum square footage.")
    price_min: Optional[float] = Field(
        None,
        ge=0,
        description="Minimum price (CAD/month when for_rent; list price CAD when for_sale).",
    )
    price_max: Optional[float] = Field(
        None,
        ge=0,
        description="Maximum price (CAD/month when for_rent; list price CAD when for_sale).",
    )
    location: str = Field(
        ...,
        min_length=1,
        description="Location string (city or 'City, Province', e.g. Vancouver or Vancouver, BC).",
    )
    listing_type: Optional[Literal["for_rent", "for_sale"]] = Field(
        default="for_rent",
        description="Transaction type: for_rent or for_sale.",
    )

    @model_validator(mode="after")
    def _validate_min_max(self) -> "RentalSearchFilters":
        _check_min_max_pairs(self)
        return self


class Listing(BaseModel):
    """§4.2 Listing (item in search results)."""

    id: str = Field(..., description="Unique identifier for the listing.")
    title: str = Field(..., description="Short title or headline.")
    url: str = Field(..., description="Canonical URL for the listing.")
    address: str = Field(..., description="Human-readable address or area.")
    price: float = Field(..., ge=0, description="Rent/price as number (for sorting).")
    price_display: Optional[str] = Field(None, description="Formatted rent/price for presentation (e.g. $2,500/month).")
    bedrooms: int = Field(..., ge=0, description="Number of bedrooms.")
    sqft: Optional[float] = Field(None, ge=0, description="Square footage.")
    source: Optional[str] = Field(None, description="Source name for display.")
    bathrooms: Optional[float] = Field(None, ge=0, description="Number of bathrooms.")
    description: Optional[str] = Field(None, description="Full or extended listing description.")
    latitude: Optional[float] = Field(None, description="Latitude.")
    longitude: Optional[float] = Field(None, description="Longitude.")
    house_category: Optional[str] = Field(None, description="Property type.")
    ownership_category: Optional[str] = Field(None, description="Ownership type.")
    ammenities: Optional[str] = Field(None, description="Listed amenities.")
    nearby_ammenities: Optional[str] = Field(None, description="Nearby features.")
    open_house: Optional[str] = Field(None, description="Open house date/time text.")
    stories: Optional[float] = Field(None, ge=0, description="Number of stories.")
    postal_code: Optional[str] = Field(None, description="Postal code.")
    parking_spaces: Optional[int] = Field(None, ge=0, description="Total number of parking spaces.")
    parking_type: Optional[str] = Field(None, description="Parking type(s), e.g. 'Garage', 'Underground'.")
    property_category: Optional[str] = Field(
        None,
        description="Broader property category (e.g. 'Single Family', 'Vacant Land', 'Multi-Family'), distinct "
        "from house_category (specific building format, e.g. 'Apartment', 'House').",
    )
    lot_size: Optional[str] = Field(None, description="Lot/land size as reported by the source (e.g. '0.25 ac', '50 x 100 ft').")
    listing_age_display: Optional[str] = Field(None, description="Human-readable listing freshness, e.g. '18 hours ago'.")
    listing_age_hours: Optional[float] = Field(
        None, ge=0, description="Approximate hours since the listing was published, parsed from listing_age_display. Smaller = newer."
    )
    photo_url: Optional[str] = Field(None, description="URL of the primary listing photo.")
    video_url: Optional[str] = Field(None, description="URL of a video or virtual tour, when available.")
    agent_name: Optional[str] = Field(None, description="Name of the (primary, when multiple) listing agent.")
    agent_phone: Optional[str] = Field(None, description="Phone number of the listing agent.")
    brokerage_name: Optional[str] = Field(None, description="Name of the listing brokerage/office.")
    price_change_display: Optional[str] = Field(
        None, description="Recent price-change signal as reported by the source (e.g. a price-reduced date/time label), when available."
    )
    listing_type: Optional[Literal["for_rent", "for_sale"]] = Field(
        None, description="Transaction type this listing was fetched as: for_rent or for_sale."
    )
    proximity: Optional[dict[str, Any]] = Field(
        None,
        description="Per-rule proximity data: keys are rule identifiers, values are { distance_km, duration_min } or null for unknown.",
    )
    semantic_score: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Cosine-similarity match score (0-1) vs. the user's qualitative preferences, "
        "set by score_listings_by_preferences. A real field (not just a display-only dict key) so "
        "it survives round-trips through Listing.model_validate() in filter_listings/"
        "enrich_listings_with_proximity — without this, filtering/enriching after scoring would "
        "silently drop the score.",
    )

    def to_short_label(self, index: Optional[int] = None) -> str:
        """Short label for approval choices, e.g. '[1] 123 Main St — $2,800/month'."""
        prefix = f"[{index}] " if index is not None else ""
        price_str = self.price_display if self.price_display else f"${int(self.price):,}"
        return f"{prefix}{self.address} — {price_str}"


class ProximityRule(BaseModel):
    """One geographic proximity constraint: location, mode, and max time/distance."""

    location: str = Field(..., description="Location string (e.g. 'downtown Vancouver', 'nearest transit station').")
    mode: Literal["drive", "walk", "transit"] = Field(..., description="Travel mode for the constraint.")
    max_minutes: float = Field(..., ge=0, description="Maximum duration in minutes.")


class GeocodedReference(BaseModel):
    """A location resolved to coordinates (from geocoding)."""

    location: str = Field(..., description="Original location string.")
    lat: float = Field(..., description="Latitude.")
    lon: float = Field(..., description="Longitude.")
    display_name: Optional[str] = Field(None, description="Human-readable name from geocoder.")


class UserDetails(BaseModel):
    """§4.3 User details (for viewing request)."""

    name: str = Field(..., min_length=1, description="User's name.")
    email: str = Field(..., description="Email for contact.")
    phone: Optional[str] = Field(None, description="Phone number.")
    preferred_times: Optional[str] = Field(None, description="Free-text viewing preference.")


class ListingFilterCriteria(BaseModel):
    """Criteria to narrow in-memory search results (input to filter_listings). All optional."""

    min_bathrooms: Optional[int] = Field(None, ge=0, description="Minimum number of bathrooms.")
    max_bathrooms: Optional[int] = Field(None, ge=0, description="Maximum number of bathrooms.")
    min_bedrooms: Optional[int] = Field(None, ge=0, description="Minimum number of bedrooms.")
    max_bedrooms: Optional[int] = Field(None, ge=0, description="Maximum number of bedrooms.")
    min_sqft: Optional[int] = Field(None, ge=0, description="Minimum square footage.")
    max_sqft: Optional[int] = Field(None, ge=0, description="Maximum square footage.")
    price_min: Optional[float] = Field(
        None, ge=0, description="Minimum price (CAD/month for rent; list price for sale)."
    )
    price_max: Optional[float] = Field(
        None, ge=0, description="Maximum price (CAD/month for rent; list price for sale)."
    )

    @model_validator(mode="after")
    def _validate_min_max(self) -> "ListingFilterCriteria":
        _check_min_max_pairs(self)
        return self


class RentalSearchResponse(BaseModel):
    """§5.2 rental_search response."""

    listings: list[Listing] = Field(..., description="List of listings.")
    total_count: int = Field(..., ge=0, description="Total number of listings.")


class AskUserAnswerResponse(BaseModel):
    """§5.1 ask_user response (single-answer mode)."""

    answer: str = Field(..., description="The chosen option or free-text reply.")


class AskUserSelectedResponse(BaseModel):
    """§5.1 ask_user response (multi-select mode)."""

    selected: list[str] = Field(default_factory=list, description="List of selected choice strings.")


class SimulateViewingRequestResponse(BaseModel):
    """§5.3 simulate_viewing_request response."""

    summary: str = Field(..., description="Human-readable summary for the agent to show the user.")
    contact_url: Optional[str] = Field(None, description="Optional mailto or contact URL.")


class AvailableSlot(BaseModel):
    """A timeslot available for a viewing."""

    start: str = Field(..., description="ISO datetime string (inclusive start).")
    end: str = Field(..., description="ISO datetime string (exclusive end).")
    display: str = Field(..., description="Human-readable slot label.")


class ViewingPlanEntry(BaseModel):
    """One entry in a viewing plan: listing mapped to a timeslot."""

    listing_id: str = Field(..., description="Listing ID.")
    listing_address: str = Field(..., description="Listing address.")
    listing_url: str = Field(..., description="Listing URL.")
    slot_display: str = Field(..., description="Human-readable timeslot.")
    start_datetime: str = Field(..., description="ISO datetime start.")
    end_datetime: str = Field(..., description="ISO datetime end.")


class ViewingPlan(BaseModel):
    """Ordered plan of listing -> timeslot mappings."""

    entries: list[ViewingPlanEntry] = Field(..., description="List of viewing plan entries.")
