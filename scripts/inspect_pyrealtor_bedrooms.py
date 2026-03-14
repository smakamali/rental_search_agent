"""
Inspect what pyRealtor returns for rentals, especially Bedrooms and 0-bed/studio/bachelor.

Run from project root:
  python scripts/inspect_pyrealtor_bedrooms.py

Or with a different city:
  python scripts/inspect_pyrealtor_bedrooms.py "Toronto"

Useful to debug why agent returns nothing for "bachelors" or "0 bed" searches.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def main():
    search_area = sys.argv[1] if len(sys.argv) > 1 else "Vancouver"
    try:
        import pyRealtor
        import pandas as pd
    except ImportError as e:
        print("Required package missing:", e)
        sys.exit(1)

    use_proxy = os.environ.get("USE_PROXY", "").lower() in ("1", "true", "yes")
    report_name = "rental_search_mvp_listings.xlsx"
    # Use project-local temp dir so script works in restricted environments (e.g. Cursor sandbox)
    project_root = os.path.join(os.path.dirname(__file__), "..")
    tmpdir = os.path.join(project_root, ".pyrealtor_inspect_tmp")
    os.makedirs(tmpdir, exist_ok=True)
    cwd = os.getcwd()
    df = None
    try:
        os.chdir(tmpdir)
        house_obj = pyRealtor.HousesFacade()
        house_obj.search_save_houses(
            search_area=search_area,
            country="Canada",
            listing_type="for_rent",
            price_from=None,
            use_proxy=use_proxy,
            report_file_name=report_name,
        )
    except Exception as e:
        os.chdir(cwd)
        print("search_save_houses failed:", e)
        raise
    finally:
        os.chdir(cwd)

    if hasattr(house_obj, "houses_df") and house_obj.houses_df is not None and not house_obj.houses_df.empty:
        df = house_obj.houses_df.copy()
    else:
        report_path = os.path.join(tmpdir, report_name)
        if os.path.isfile(report_path):
            df = pd.read_excel(report_path, sheet_name="Listings")
        else:
            print("No houses_df and no Excel file from pyRealtor.")
            return

    print("=" * 60)
    print(f"pyRealtor search: {search_area}, for_rent")
    print("=" * 60)
    print(f"Total rows: {len(df)}")
    print()
    print("Columns:")
    for i, col in enumerate(df.columns.tolist(), 1):
        print(f"  {i}. {col}")
    print()

    # Bedrooms column
    if "Bedrooms" in df.columns:
        bed = df["Bedrooms"]
        print("--- Bedrooms column ---")
        print(f"  dtype: {bed.dtype}")
        print(f"  unique values (raw): {sorted(bed.dropna().unique().tolist())}")
        print(f"  value_counts:\n{bed.value_counts(dropna=False).to_string()}")
        # Try numeric view
        try:
            bed_numeric = pd.to_numeric(bed, errors="coerce")
            print(f"  as numeric - unique: {sorted(bed_numeric.dropna().unique().tolist())}")
            print(f"  min: {bed_numeric.min()}, max: {bed_numeric.max()}")
        except Exception as e:
            print(f"  to_numeric error: {e}")
        print()
        # Rows that might be 0-bed or studio
        low_bed = df[pd.to_numeric(df["Bedrooms"], errors="coerce") <= 1]
        print(f"  Rows with Bedrooms <= 1 (or non-numeric): {len(low_bed)}")
        if len(low_bed) > 0:
            print("  Sample (first 5) - Bedrooms, Address, Rent/Total Rent, House Category:")
            subset = low_bed.head(5)
            for _, row in subset.iterrows():
                addr = row.get("Address", "")
                rent = row.get("Total Rent", row.get("Rent", ""))
                cat = row.get("House Category", "")
                print(f"    Bedrooms={row.get('Bedrooms')!r} | {addr!r} | Rent={rent} | House Category={cat!r}")
    else:
        print("No 'Bedrooms' column in DataFrame.")
    print()

    # House Category (might show "Condo", "Apartment", "Studio", etc.)
    if "House Category" in df.columns:
        print("--- House Category ---")
        print(df["House Category"].value_counts(dropna=False).to_string())
        print()

    # Description snippet for low-bed listings (might contain "studio"/"bachelor")
    if "Bedrooms" in df.columns and "Description" in df.columns:
        low_bed = df[pd.to_numeric(df["Bedrooms"], errors="coerce") <= 1]
        if len(low_bed) > 0:
            print("--- Description snippets (Bedrooms <= 1) ---")
            for i, (_, row) in enumerate(low_bed.head(5).iterrows()):
                desc = (row.get("Description") or "")[:200]
                print(f"  [{i+1}] {desc!r}...")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
