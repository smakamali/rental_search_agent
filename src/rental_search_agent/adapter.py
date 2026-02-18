"""Rental search backend adapter: pyRealtor → Listing shape. Per spec §6."""

import os
import re
import tempfile
from typing import Optional

import pandas as pd

from rental_search_agent.models import Listing, RentalSearchFilters, RentalSearchResponse


class SearchBackendError(Exception):
    """Raised when the rental search backend fails (timeout, error). Do not return empty list."""

    pass


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """Coerce series to numeric; invalid/missing become NaN."""
    return pd.to_numeric(series.astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce")


def _parse_sqft(val) -> Optional[float]:
    """Parse Size column (may be '1200 sqft' or number) to float sqft."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    match = re.search(r"[\d.]+", s)
    if match:
        return float(match.group())
    return None


def _row_to_listing(row: pd.Series, listing_type: str) -> Listing:
    """Map one DataFrame row (pyRealtor output) to Listing."""
    price_col = "Rent" if listing_type == "for_rent" else "Price"
    price_val = row.get(price_col)
    if price_val is not None and not (isinstance(price_val, float) and pd.isna(price_val)):
        try:
            price = float(re.sub(r"[^\d.]", "", str(price_val)))
        except (ValueError, TypeError):
            price = 0.0
    else:
        price = 0.0

    bedrooms_val = row.get("Bedrooms")
    bedrooms = int(_coerce_numeric(pd.Series([bedrooms_val])).iloc[0]) if bedrooms_val is not None else 0
    if pd.isna(bedrooms) or bedrooms < 0:
        bedrooms = 0

    url = str(row.get("Website", "") or "").strip() or f"https://www.realtor.ca/listing/{row.get('MLS', '')}"
    title = str(row.get("Description", "") or "")[:200] or f"Listing {row.get('MLS', '')}"
    address = str(row.get("Address", "") or "").strip() or "Address not provided"

    return Listing(
        id=str(row.get("MLS", "") or ""),
        title=title,
        url=url,
        address=address,
        price=price,
        bedrooms=bedrooms,
        sqft=_parse_sqft(row.get("Size")),
        source="Realtor.ca",
        bathrooms=_coerce_numeric(pd.Series([row.get("Bathrooms")])).iloc[0] if row.get("Bathrooms") is not None else None,
        description=str(row.get("Description", "") or "") if row.get("Description") else None,
        latitude=float(row["Latitude"]) if pd.notna(row.get("Latitude")) and str(row.get("Latitude")).strip() else None,
        longitude=float(row["Longitude"]) if pd.notna(row.get("Longitude")) and str(row.get("Longitude")).strip() else None,
        house_category=str(row.get("House Category", "") or "").strip() or None,
        ownership_category=str(row.get("Ownership Category", "") or "").strip() or None,
        ammenities=str(row.get("Ammenities", "") or "").strip() or None,
        nearby_ammenities=str(row.get("Nearby Ammenities", "") or "").strip() or None,
        open_house=str(row.get("Open House", "") or "").strip() or None,
        stories=float(row["Stories"]) if pd.notna(row.get("Stories")) and str(row.get("Stories")).strip() else None,
    )


def search(filters: RentalSearchFilters, use_proxy: bool = False) -> RentalSearchResponse:
    """
    Run a single logical search via pyRealtor; post-filter and map to Listing.
    On backend failure, raises SearchBackendError (do not return empty list).
    """
    try:
        import pyRealtor
    except ImportError as e:
        raise SearchBackendError("Rental search backend (pyRealtor) is not available.") from e

    listing_type = filters.listing_type or "for_rent"
    use_proxy = use_proxy or (os.environ.get("USE_PROXY", "").lower() in ("1", "true", "yes"))

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        report_name = "rental_search_mvp_listings.xlsx"
        try:
            os.chdir(tmpdir)
            house_obj = pyRealtor.HousesFacade()
            house_obj.search_save_houses(
                search_area=filters.location,
                country="Canada",
                listing_type=listing_type,
                price_from=int(filters.rent_min) if filters.rent_min is not None else None,
                use_proxy=use_proxy,
                report_file_name=report_name,
            )
        except Exception as e:
            os.chdir(cwd)
            raise SearchBackendError("The rental search is temporarily unavailable.") from e
        finally:
            os.chdir(cwd)

        try:
            if hasattr(house_obj, "houses_df") and house_obj.houses_df is not None and not house_obj.houses_df.empty:
                df = house_obj.houses_df.copy()
            else:
                report_path = os.path.join(tmpdir, report_name)
                df = pd.read_excel(report_path, sheet_name="Listings")
        except Exception as e:
            raise SearchBackendError("The rental search is temporarily unavailable.") from e

    if df.empty:
        return RentalSearchResponse(listings=[], total_count=0)

    price_col = "Rent" if listing_type == "for_rent" else "Price"
    if price_col not in df.columns:
        price_col = "Price" if "Price" in df.columns else None
    if price_col is None:
        return RentalSearchResponse(listings=[], total_count=0)

    df["_bedrooms"] = _coerce_numeric(df.get("Bedrooms", pd.Series(dtype=float)))
    df["_bathrooms"] = _coerce_numeric(df.get("Bathrooms", pd.Series(dtype=float)))
    df["_size"] = df.get("Size", pd.Series(dtype=object)).apply(lambda x: _parse_sqft(x))
    df["_price"] = _coerce_numeric(df.get(price_col, pd.Series(dtype=float)))

    mask = pd.Series(True, index=df.index)
    if filters.min_bedrooms is not None:
        mask &= df["_bedrooms"] >= filters.min_bedrooms
    if filters.max_bedrooms is not None:
        mask &= df["_bedrooms"] <= filters.max_bedrooms
    if filters.min_bathrooms is not None:
        mask &= df["_bathrooms"] >= filters.min_bathrooms
    if filters.max_bathrooms is not None:
        mask &= df["_bathrooms"] <= filters.max_bathrooms
    if filters.min_sqft is not None:
        mask &= (df["_size"].notna()) & (df["_size"] >= filters.min_sqft)
    if filters.max_sqft is not None:
        mask &= (df["_size"].notna()) & (df["_size"] <= filters.max_sqft)
    if filters.rent_min is not None:
        mask &= df["_price"] >= filters.rent_min
    if filters.rent_max is not None:
        mask &= df["_price"] <= filters.rent_max

    df = df.loc[mask].drop(columns=["_bedrooms", "_bathrooms", "_size", "_price"], errors="ignore")

    listings = [_row_to_listing(row, listing_type) for _, row in df.iterrows()]
    return RentalSearchResponse(listings=listings, total_count=len(listings))
