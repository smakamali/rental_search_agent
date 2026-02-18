"""Data models per technical spec §4 and §5."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RentalSearchFilters(BaseModel):
    """§4.1 Rental search filters (input to rental_search)."""

    min_bedrooms: int = Field(..., ge=0, description="Minimum number of bedrooms.")
    max_bedrooms: Optional[int] = Field(None, ge=0, description="Maximum number of bedrooms.")
    min_bathrooms: Optional[int] = Field(None, ge=0, description="Minimum number of bathrooms.")
    max_bathrooms: Optional[int] = Field(None, ge=0, description="Maximum number of bathrooms.")
    min_sqft: Optional[int] = Field(None, ge=0, description="Minimum square footage.")
    max_sqft: Optional[int] = Field(None, ge=0, description="Maximum square footage.")
    rent_min: Optional[float] = Field(None, ge=0, description="Minimum rent (CAD/month).")
    rent_max: Optional[float] = Field(None, ge=0, description="Maximum rent (CAD/month).")
    location: str = Field(..., min_length=1, description="Location string (e.g. city or area name).")
    listing_type: Optional[Literal["for_rent", "for_sale", "for_sale_or_rent"]] = Field(
        default="for_rent",
        description="Transaction type.",
    )


class Listing(BaseModel):
    """§4.2 Listing (item in search results)."""

    id: str = Field(..., description="Unique identifier for the listing.")
    title: str = Field(..., description="Short title or headline.")
    url: str = Field(..., description="Canonical URL for the listing.")
    address: str = Field(..., description="Human-readable address or area.")
    price: float = Field(..., ge=0, description="Rent in CAD/month.")
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

    def to_short_label(self, index: Optional[int] = None) -> str:
        """Short label for approval choices, e.g. '[1] 123 Main St — $2800'."""
        prefix = f"[{index}] " if index is not None else ""
        return f"{prefix}{self.address} — ${int(self.price)}"


class UserDetails(BaseModel):
    """§4.3 User details (for viewing request)."""

    name: str = Field(..., min_length=1, description="User's name.")
    email: str = Field(..., description="Email for contact.")
    phone: Optional[str] = Field(None, description="Phone number.")
    preferred_times: Optional[str] = Field(None, description="Free-text viewing preference.")


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
