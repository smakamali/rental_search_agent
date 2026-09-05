"""Apify Realtor.ca backend: igolaizola/realtor-canada-scraper-ppe → Listing."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlparse

from rental_search_agent.backends.common import (
    _coerce_float,
    _coerce_int,
    _format_price_display,
    _parse_sqft,
    post_filter_listings,
)
from rental_search_agent.backends.errors import SearchBackendError
from rental_search_agent.models import Listing, RentalSearchFilters, RentalSearchResponse

logger = logging.getLogger(__name__)

DEFAULT_ACTOR_ID = "igolaizola/realtor-canada-scraper-ppe"
DEFAULT_MAX_ITEMS = 100
# Bound how long we wait for the Apify actor run to finish; without this the
# apify-client SDK waits indefinitely, which would hang the whole agent turn.
ACTOR_CALL_WAIT_DURATION = timedelta(minutes=2)
# Only trust absolute listing URLs on these hosts; anything else from the (third-party,
# scraped) dataset falls back to an MLS-based realtor.ca URL to avoid propagating
# attacker-controlled off-site links into the UI/calendar.
_ALLOWED_URL_HOSTS = {"realtor.ca", "www.realtor.ca"}


def _call_actor(actor_client: Any, run_input: dict[str, Any], wait: timedelta) -> Any:
    """Start an Apify actor run and wait for it to finish.

    apify-client's Actor.call() signature changed across major versions
    (wait_duration: timedelta on >= 3.0 vs wait_secs: int on < 3.0), and which major
    version gets installed depends on the host's Python version (>= 3.0 requires
    Python >= 3.11, so Python 3.10 envs resolve to a 2.x release). Try the new-style
    kwarg first and fall back to the old one so this works with either.
    """
    try:
        return actor_client.call(run_input=run_input, wait_duration=wait)
    except TypeError:
        return actor_client.call(run_input=run_input, wait_secs=int(wait.total_seconds()))


def _run_field(run: Any, snake_name: str, camel_name: str) -> Any:
    """Read a field off an Apify actor Run result.

    apify-client >= 3.0 returns a pydantic Run model (snake_case attributes);
    apify-client < 3.0 returns a plain dict (camelCase keys, per the Apify API).
    """
    if isinstance(run, dict):
        return run.get(camel_name)
    return getattr(run, snake_name, None)


def filters_to_run_input(filters: RentalSearchFilters, max_items: int) -> dict[str, Any]:
    """Map RentalSearchFilters to igolaizola actor run_input."""
    listing_type = filters.listing_type or "for_rent"
    if listing_type not in ("for_rent", "for_sale"):
        raise SearchBackendError(
            "listing_type must be 'for_rent' or 'for_sale'. "
            "Choose rent or sale — combined searches are not supported yet."
        )
    operation = "rent" if listing_type == "for_rent" else "buy"
    run_input: dict[str, Any] = {
        "maxItems": max_items,
        "location": filters.location,
        "operation": operation,
        "sortBy": "newest",
        "minBeds": filters.min_bedrooms,
        "maxBeds": 0,
        "minBathrooms": 0,
        "maxBathrooms": 0,
        "minPrice": 0,
        "maxPrice": 0,
        "minSquareFootage": 0,
        "maxSquareFootage": 0,
    }
    if filters.max_bedrooms is not None:
        run_input["maxBeds"] = filters.max_bedrooms
    if filters.min_bathrooms is not None:
        run_input["minBathrooms"] = filters.min_bathrooms
    if filters.max_bathrooms is not None:
        run_input["maxBathrooms"] = filters.max_bathrooms
    if filters.price_min is not None:
        run_input["minPrice"] = round(filters.price_min)
    if filters.price_max is not None:
        run_input["maxPrice"] = round(filters.price_max)
    if filters.min_sqft is not None:
        run_input["minSquareFootage"] = filters.min_sqft
    if filters.max_sqft is not None:
        run_input["maxSquareFootage"] = filters.max_sqft
    return run_input


def item_to_listing(item: dict[str, Any], listing_type: str) -> Listing:
    """Map one igolaizola / Realtor.ca-style dataset item to Listing."""
    prop = item.get("Property") if isinstance(item.get("Property"), dict) else {}
    building = item.get("Building") if isinstance(item.get("Building"), dict) else {}
    address_obj = prop.get("Address") if isinstance(prop.get("Address"), dict) else {}

    mls = str(item.get("MlsNumber") or item.get("Id") or "").strip()
    relative = item.get("RelativeURLEn") or item.get("URL") or ""
    raw_url = str(relative).strip()
    fallback_url = f"https://www.realtor.ca/listing/{mls}" if mls else "https://www.realtor.ca"
    if raw_url.startswith("/"):
        url = "https://www.realtor.ca" + raw_url
    elif raw_url.startswith("http://") or raw_url.startswith("https://"):
        host = (urlparse(raw_url).hostname or "").lower()
        # Only trust absolute URLs on realtor.ca; the actor dataset is third-party
        # scraped data and shouldn't be able to inject arbitrary off-site links.
        url = raw_url if host in _ALLOWED_URL_HOSTS else fallback_url
    else:
        url = fallback_url

    # Address may be nested or a flat string on some payloads. PostalCode lives at the
    # item root (not under Property.Address) on real igolaizola payloads; check both.
    if address_obj:
        address_raw = (
            str(address_obj.get("AddressText") or address_obj.get("Text") or "").strip()
        )
        # Real payloads join street/city/province with "|" (e.g. "123 Main St|Vancouver,
        # British Columbia V6B1A1"); render as a normal comma-separated address.
        address = address_raw.replace("|", ", ") or "Address not provided"
        postal = str(address_obj.get("PostalCode") or item.get("PostalCode") or "").strip() or None
        lat = _coerce_float(address_obj.get("Latitude"))
        lon = _coerce_float(address_obj.get("Longitude"))
    else:
        address = str(item.get("Address") or prop.get("Address") or "").strip().replace("|", ", ") or "Address not provided"
        postal = str(item.get("PostalCode") or "").strip() or None
        lat = _coerce_float(item.get("Latitude") or prop.get("Latitude"))
        lon = _coerce_float(item.get("Longitude") or prop.get("Longitude"))

    # Rent listings use LeaseRent/LeaseRentUnformattedValue; sale listings use
    # Price/PriceUnformattedValue. Check the type-appropriate fields first, but fall
    # back to the other pair defensively in case the actor's payload shape varies.
    if listing_type == "for_rent":
        price_fields = ("LeaseRentUnformattedValue", "PriceUnformattedValue")
        price_raw_fields = ("LeaseRent", "Price")
    else:
        price_fields = ("PriceUnformattedValue", "LeaseRentUnformattedValue")
        price_raw_fields = ("Price", "LeaseRent")
    price_candidates: list[Optional[float]] = []
    for field in price_fields:
        price_candidates.append(_coerce_float(prop.get(field)))
        price_candidates.append(_coerce_float(item.get(field)))
    price = next((v for v in price_candidates if v is not None), 0.0)
    price_raw = next(
        (prop.get(field) or item.get(field) for field in price_raw_fields if prop.get(field) or item.get(field)),
        prop.get("ShortValue"),
    )
    price_display = _format_price_display(price_raw, price, listing_type)

    bedrooms = _coerce_int(
        building.get("Bedrooms")
        or building.get("BedroomsTotal")
        or item.get("Bedrooms")
        or item.get("BedroomsTotal"),
        default=0,
    )
    bathrooms = _coerce_float(
        building.get("BathroomTotal")
        or building.get("Bathrooms")
        or item.get("Bathrooms")
        or item.get("BathroomTotal")
    )
    sqft = _parse_sqft(
        building.get("SizeInterior")
        or building.get("InteriorSize")
        or item.get("SizeInterior")
        or item.get("sqft")
    )
    stories = _coerce_float(building.get("Stories") or item.get("Stories"))
    # Property.ParkingSpaceTotal/ParkingType are explicit structured fields on the actor
    # payload (not present on every listing, e.g. some condos have no dedicated parking).
    parking_spaces_val = _coerce_float(prop.get("ParkingSpaceTotal") or item.get("ParkingSpaceTotal"))
    parking_spaces = int(parking_spaces_val) if parking_spaces_val is not None else None
    parking_type = str(prop.get("ParkingType") or item.get("ParkingType") or "").strip() or None
    description = item.get("PublicRemarks") or item.get("Description") or prop.get("Description")
    description_str = str(description).strip() if description else None
    title = (description_str[:200] if description_str else None) or (f"Listing {mls}" if mls else "Listing")

    house_category = (
        str(building.get("Type") or building.get("BuildingType") or item.get("BuildingType") or "").strip()
        or None
    )
    ownership = str(item.get("OwnershipType") or prop.get("OwnershipType") or "").strip() or None

    return Listing(
        id=mls or str(item.get("Id") or ""),
        title=title,
        url=url,
        address=address,
        price=price,
        price_display=price_display,
        bedrooms=bedrooms,
        sqft=sqft,
        source="Realtor.ca",
        bathrooms=bathrooms,
        description=description_str,
        latitude=lat,
        longitude=lon,
        house_category=house_category,
        ownership_category=ownership,
        stories=stories,
        parking_spaces=parking_spaces,
        parking_type=parking_type,
        listing_type=listing_type if listing_type in ("for_rent", "for_sale") else None,
        postal_code=postal,
    )


class ApifyRealtorCaBackend:
    """Canada Realtor.ca search via Apify actor igolaizola/realtor-canada-scraper-ppe."""

    def __init__(
        self,
        token: Optional[str] = None,
        actor_id: Optional[str] = None,
        max_items: Optional[int] = None,
        client: Any = None,
    ) -> None:
        self.token = token if token is not None else (os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (
            actor_id
            or (os.environ.get("APIFY_ACTOR_ID") or "").strip()
            or DEFAULT_ACTOR_ID
        )
        raw_max = max_items
        if raw_max is None:
            try:
                raw_max = int(os.environ.get("APIFY_MAX_ITEMS") or DEFAULT_MAX_ITEMS)
            except ValueError:
                raw_max = DEFAULT_MAX_ITEMS
        self.max_items = max(1, int(raw_max))
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.token:
            raise SearchBackendError(
                "APIFY_TOKEN is not set. Add your Apify API token to .env to run property search."
            )
        try:
            from apify_client import ApifyClient
        except ImportError as e:
            raise SearchBackendError(
                "Rental search backend (apify-client) is not available."
            ) from e
        return ApifyClient(self.token)

    def search(self, filters: RentalSearchFilters) -> RentalSearchResponse:
        listing_type = filters.listing_type or "for_rent"
        run_input = filters_to_run_input(filters, self.max_items)
        client = self._get_client()
        try:
            logger.debug(
                "Apify actor=%s operation=%s location=%s maxItems=%s",
                self.actor_id,
                run_input.get("operation"),
                run_input.get("location"),
                run_input.get("maxItems"),
            )
            run = _call_actor(client.actor(self.actor_id), run_input, ACTOR_CALL_WAIT_DURATION)
        except SearchBackendError:
            raise
        except Exception as e:
            logger.warning("Apify search failed: %s: %s", type(e).__name__, e)
            raise SearchBackendError("The rental search is temporarily unavailable.") from e

        # client.actor(...).call() returns an apify_client Run object (attribute access) on
        # apify-client >= 3.0, or a plain dict (camelCase keys) on < 3.0 — see _run_field().
        dataset_id = _run_field(run, "default_dataset_id", "defaultDatasetId")
        if not run or not dataset_id:
            raise SearchBackendError("The rental search is temporarily unavailable.")

        status = _run_field(run, "status", "status")
        if status != "SUCCEEDED":
            logger.warning("Apify run did not succeed: status=%s", status)
            if status in ("RUNNING", "READY", "TIMING-OUT", "ABORTING"):
                raise SearchBackendError(
                    "The property search is taking longer than expected. Please try again in a moment."
                )
            raise SearchBackendError("The rental search is temporarily unavailable.")

        try:
            items = list(client.dataset(dataset_id).iterate_items())
        except Exception as e:
            logger.warning("Apify dataset read failed: %s: %s", type(e).__name__, e)
            raise SearchBackendError("The rental search is temporarily unavailable.") from e

        listings = [item_to_listing(item, listing_type) for item in items if isinstance(item, dict)]
        before = len(listings)
        listings = post_filter_listings(listings, filters)
        if before != len(listings):
            logger.debug(
                "Post-fetch filter excluded %d of %d listings",
                before - len(listings),
                before,
            )
        return RentalSearchResponse(listings=listings, total_count=len(listings))
