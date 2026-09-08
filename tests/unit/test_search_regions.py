"""Unit tests for search_regions catalog and expand_search_region."""

from rental_search_agent.search_regions import (
    METRO_VANCOUVER,
    GREATER_TORONTO,
    canonicalize_search_locations,
    expand_search_region,
    known_region_names,
    lookup_region,
    resolve_search_location_input,
    unique_search_locations,
)
from rental_search_agent.models import MAX_SEARCH_LOCATIONS


class TestLookupRegion:
    def test_metro_vancouver_aliases(self):
        for name in ("Metro Vancouver", "greater vancouver", "GVA", "metro vancouver, bc"):
            match = lookup_region(name)
            assert match is not None
            assert match.name == "Metro Vancouver"

    def test_gta_aliases(self):
        for name in ("GTA", "Greater Toronto Area", "metro toronto"):
            match = lookup_region(name)
            assert match is not None
            assert match.name == "Greater Toronto Area"

    def test_bare_city_does_not_match(self):
        assert lookup_region("Vancouver") is None
        assert lookup_region("Toronto") is None
        assert lookup_region("Hamilton") is None
        assert lookup_region("Ottawa") is None
        assert lookup_region("Calgary") is None
        assert lookup_region("Edmonton") is None

    def test_unknown_returns_none(self):
        assert lookup_region("the island") is None
        assert lookup_region("") is None


class TestExpandSearchRegion:
    def test_metro_vancouver_returns_picker_rows(self):
        result = expand_search_region("Metro Vancouver")
        assert result["region"] == "Metro Vancouver"
        labels = [c["label"] for c in result["cities"]]
        assert "Vancouver" in labels
        assert "Burnaby" in labels
        assert "City of North Vancouver" in labels
        assert "District of North Vancouver" in labels
        assert "Township of Langley" in labels
        assert len(result["cities"]) >= 21

    def test_unknown_returns_error(self):
        result = expand_search_region("the Prairies")
        assert "error" in result
        assert "Unknown region" in result["error"]
        for name in known_region_names():
            assert name in result["error"]

    def test_empty_returns_error(self):
        result = expand_search_region("  ")
        assert "error" in result


class TestUniqueSearchLocations:
    def test_north_vancouver_city_and_district_share_search_string(self):
        locs = unique_search_locations(METRO_VANCOUVER.cities)
        north = [c for c in METRO_VANCOUVER.cities if "North Vancouver" in c.label]
        assert len(north) == 2
        assert north[0].search_location == north[1].search_location
        assert locs.count("North Vancouver, BC") == 1
        assert len(locs) < len(METRO_VANCOUVER.cities)


class TestCanonicalizeSearchLocations:
    def test_picker_labels_collapse_north_vancouver_to_one_scrape(self):
        locs = canonicalize_search_locations(
            ["City of North Vancouver", "District of North Vancouver"]
        )
        assert locs == ["North Vancouver, BC"]

    def test_unknown_city_kept_as_is(self):
        assert canonicalize_search_locations(["Nanaimo, BC"]) == ["Nanaimo, BC"]

    def test_maps_catalog_label_to_search_location(self):
        assert canonicalize_search_locations(["Burnaby"]) == ["Burnaby, BC"]


class TestCatalogFitsCap:
    def test_metro_vancouver_picker_fits_cap(self):
        assert len(METRO_VANCOUVER.cities) <= MAX_SEARCH_LOCATIONS
        assert len(unique_search_locations(METRO_VANCOUVER.cities)) <= MAX_SEARCH_LOCATIONS

    def test_gta_picker_fits_cap(self):
        assert len(GREATER_TORONTO.cities) <= MAX_SEARCH_LOCATIONS
        assert len(unique_search_locations(GREATER_TORONTO.cities)) <= MAX_SEARCH_LOCATIONS


class TestResolveSearchLocationInput:
    def test_metro_vancouver_expands_to_city_list(self):
        resolved = resolve_search_location_input("Metro Vancouver")
        assert isinstance(resolved, list)
        assert "Vancouver, BC" in resolved
        assert "Burnaby, BC" in resolved
        assert len(resolved) > 1
        assert len(resolved) <= MAX_SEARCH_LOCATIONS

    def test_gta_alias_expands(self):
        resolved = resolve_search_location_input("GTA")
        assert isinstance(resolved, list)
        assert "Toronto, ON" in resolved
        assert len(resolved) > 1
        assert len(resolved) <= MAX_SEARCH_LOCATIONS

    def test_bare_vancouver_is_single_city(self):
        assert resolve_search_location_input("Vancouver") == "Vancouver"

    def test_city_with_province_unchanged(self):
        assert resolve_search_location_input("Vancouver, BC") == "Vancouver, BC"

    def test_blank_returns_empty(self):
        assert resolve_search_location_input("  ") == ""

    def test_unknown_text_treated_as_city(self):
        assert resolve_search_location_input("the Prairies") == "the Prairies"

    def test_metro_list_matches_catalog_unique_locations(self):
        region = lookup_region("Metro Vancouver")
        assert region is not None
        expected = unique_search_locations(region.cities)
        assert resolve_search_location_input("Greater Vancouver") == expected
