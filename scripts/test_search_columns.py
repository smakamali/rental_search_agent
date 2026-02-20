"""Test run search_save_houses and list columns in house_obj.houses_df."""
import os
import sys
import tempfile

# Ensure package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def main():
    try:
        import pyRealtor
    except ImportError as e:
        print("pyRealtor not installed:", e)
        sys.exit(1)

    use_proxy = os.environ.get("USE_PROXY", "").lower() in ("1", "true", "yes")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            os.chdir(tmpdir)
            house_obj = pyRealtor.HousesFacade()
            house_obj.search_save_houses(
                search_area="Vancouver",
                country="Canada",
                listing_type="for_rent",
                price_from=None,
                use_proxy=use_proxy,
                report_file_name="rental_search_mvp_listings.xlsx",
            )
        except Exception as e:
            os.chdir(cwd)
            print("search_save_houses failed:", e)
            raise
        finally:
            os.chdir(cwd)

    if hasattr(house_obj, "houses_df") and house_obj.houses_df is not None:
        df = house_obj.houses_df
        print("house_obj.houses_df columns:")
        for i, col in enumerate(df.columns.tolist(), 1):
            print(f"  {i}. {col}")
        print(f"\nTotal columns: {len(df.columns)}")
        print(f"Rows: {len(df)}")
    else:
        print("house_obj.houses_df is missing or None")


if __name__ == "__main__":
    main()
