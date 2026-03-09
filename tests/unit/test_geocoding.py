"""Unit tests for rental_search_agent.geocoding."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

import rental_search_agent.geocoding as geocoding_module
from rental_search_agent.geocoding import (
    NEAREST_TRANSIT_LOCATION,
    geocode_location,
    geocode_proximity_references,
)
from rental_search_agent.models import GeocodedReference, ProximityRule


def _make_urlopen_response(payload: dict):
    """Return a mock context manager that yields a fake HTTP response."""
    body = json.dumps(payload).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _ok_geocode_response(lat=49.28, lon=-123.12, address="Vancouver, BC, Canada"):
    return {
        "status": "OK",
        "results": [
            {
                "formatted_address": address,
                "geometry": {
                    "location": {"lat": lat, "lng": lon}
                },
            }
        ],
    }


@pytest.fixture(autouse=True)
def clear_geocode_cache():
    """Clear the in-memory geocode cache before each test."""
    geocoding_module._GEOCODE_CACHE.clear()
    yield
    geocoding_module._GEOCODE_CACHE.clear()


class TestGeocodeLocation:
    def test_returns_geocoded_reference_on_ok(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_ok_geocode_response())):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                ref = geocode_location("Vancouver, BC")
        assert isinstance(ref, GeocodedReference)
        assert ref.lat == pytest.approx(49.28)
        assert ref.lon == pytest.approx(-123.12)
        assert ref.display_name == "Vancouver, BC, Canada"
        assert ref.location == "Vancouver, BC"

    def test_raises_for_non_ok_status(self):
        payload = {"status": "ZERO_RESULTS", "results": []}
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                with pytest.raises(ValueError, match="ZERO_RESULTS"):
                    geocode_location("Nowhere Land")

    def test_raises_for_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="GOOGLE_MAPS_API_KEY"):
                geocode_location("Vancouver")

    def test_raises_for_empty_location(self):
        with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
            with pytest.raises(ValueError):
                geocode_location("")

    def test_raises_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                with pytest.raises(ValueError, match="Geocoding failed"):
                    geocode_location("Vancouver")

    def test_raises_when_results_empty(self):
        payload = {"status": "OK", "results": []}
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                with pytest.raises(ValueError, match="No geocoding results"):
                    geocode_location("Nonexistent Place")

    def test_raises_when_lat_lng_missing(self):
        payload = {
            "status": "OK",
            "results": [
                {"formatted_address": "X", "geometry": {"location": {}}}
            ],
        }
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                with pytest.raises(ValueError, match="Missing lat/lng"):
                    geocode_location("Some Place")

    def test_caches_result_on_second_call(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_ok_geocode_response())) as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                ref1 = geocode_location("Vancouver, BC")
                ref2 = geocode_location("Vancouver, BC")
        assert mock_open.call_count == 1
        assert ref1.lat == ref2.lat
        assert ref1.lon == ref2.lon

    def test_cache_key_is_case_insensitive(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(_ok_geocode_response())) as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                geocode_location("vancouver, bc")
                geocode_location("Vancouver, BC")
        assert mock_open.call_count == 1

    def test_uses_location_as_display_name_fallback(self):
        payload = {
            "status": "OK",
            "results": [
                {"geometry": {"location": {"lat": 49.0, "lng": -123.0}}}
            ],
        }
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                ref = geocode_location("MyPlace")
        assert ref.display_name == "MyPlace"


class TestGeocodeProximityReferences:
    def test_skips_nearest_transit_station_rule(self):
        rules = [
            ProximityRule(location=NEAREST_TRANSIT_LOCATION, mode="walk", max_minutes=5)
        ]
        with patch("urllib.request.urlopen") as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                refs = geocode_proximity_references(rules)
        assert refs == []
        mock_open.assert_not_called()

    def test_geocodes_non_transit_rules(self):
        rules = [
            ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=30),
            ProximityRule(location="UBC", mode="transit", max_minutes=45),
        ]
        responses = [
            _ok_geocode_response(lat=49.28, lon=-123.12, address="Downtown Vancouver"),
            _ok_geocode_response(lat=49.26, lon=-123.25, address="UBC"),
        ]
        side_effects = [_make_urlopen_response(r) for r in responses]
        with patch("urllib.request.urlopen", side_effect=side_effects):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                refs = geocode_proximity_references(rules)
        assert len(refs) == 2
        assert refs[0].location == "Downtown Vancouver"
        assert refs[1].location == "UBC"

    def test_skips_rules_that_fail_geocoding(self):
        rules = [
            ProximityRule(location="Good Place", mode="drive", max_minutes=20),
            ProximityRule(location="Bad Place", mode="drive", max_minutes=20),
        ]
        def urlopen_side_effect(url, timeout=None):
            if "Good+Place" in url or "Good%20Place" in url or "Good" in url:
                return _make_urlopen_response(_ok_geocode_response(lat=49.0, lon=-123.0, address="Good Place"))
            raise OSError("network error")

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                refs = geocode_proximity_references(rules)
        assert len(refs) == 1
        assert refs[0].location == "Good Place"

    def test_mixed_transit_and_fixed_rules(self):
        rules = [
            ProximityRule(location=NEAREST_TRANSIT_LOCATION, mode="walk", max_minutes=5),
            ProximityRule(location="Surrey Central", mode="drive", max_minutes=20),
        ]
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(
            _ok_geocode_response(lat=49.19, lon=-122.85, address="Surrey Central")
        )):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                refs = geocode_proximity_references(rules)
        assert len(refs) == 1
        assert refs[0].location == "Surrey Central"

    def test_empty_rules_returns_empty(self):
        refs = geocode_proximity_references([])
        assert refs == []
