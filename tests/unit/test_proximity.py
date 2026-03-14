"""Unit tests for rental_search_agent.proximity."""

import json
from unittest.mock import MagicMock, patch

import pytest

import rental_search_agent.proximity as proximity_module
from rental_search_agent.proximity import (
    _get_distance_matrix_batch,
    enrich_listings_with_proximity,
    get_nearest_transit_station,
)
from rental_search_agent.models import GeocodedReference, ProximityRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_urlopen_response(payload: dict):
    body = json.dumps(payload).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _places_ok_response(lat=49.2701, lon=-123.0, name="Nanaimo Station"):
    return {
        "status": "OK",
        "results": [
            {
                "name": name,
                "geometry": {"location": {"lat": lat, "lng": lon}},
            }
        ],
    }


def _distance_matrix_ok_response(dist_m=5000, dur_s=600):
    return {
        "status": "OK",
        "rows": [
            {
                "elements": [
                    {
                        "status": "OK",
                        "distance": {"value": dist_m},
                        "duration": {"value": dur_s},
                    }
                ]
            }
        ],
    }


def _directions_ok_response(dist_m=3000, dur_s=420):
    return {
        "status": "OK",
        "routes": [
            {
                "legs": [
                    {
                        "distance": {"value": dist_m},
                        "duration": {"value": dur_s},
                    }
                ]
            }
        ],
    }


def _sample_listing_dict(id="lst-1", lat=49.28, lon=-123.12):
    return {
        "id": id,
        "title": f"Listing {id}",
        "address": f"Address {id}",
        "url": f"https://example.com/{id}",
        "bedrooms": 2,
        "price": 2500,
        "latitude": lat,
        "longitude": lon,
    }


# ---------------------------------------------------------------------------
# Fixtures: clear module-level caches between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_proximity_caches():
    proximity_module._PLACES_CACHE.clear()
    proximity_module._DIRECTIONS_CACHE.clear()
    yield
    proximity_module._PLACES_CACHE.clear()
    proximity_module._DIRECTIONS_CACHE.clear()


# ---------------------------------------------------------------------------
# Tests: get_nearest_transit_station
# ---------------------------------------------------------------------------

class TestGetNearestTransitStation:
    def test_returns_station_on_ok_response(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(
            _places_ok_response(lat=49.2701, lon=-123.0, name="Nanaimo Station")
        )):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = get_nearest_transit_station(49.28, -123.12)

        assert result is not None
        lat, lon, name = result
        assert lat == pytest.approx(49.2701)
        assert lon == pytest.approx(-123.0)
        assert name == "Nanaimo Station"

    def test_returns_none_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = get_nearest_transit_station(49.28, -123.12)
        assert result is None

    def test_returns_none_on_zero_results(self):
        payload = {"status": "ZERO_RESULTS", "results": []}
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = get_nearest_transit_station(49.28, -123.12)
        assert result is None

    def test_returns_none_on_empty_results_list(self):
        payload = {"status": "OK", "results": []}
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = get_nearest_transit_station(49.28, -123.12)
        assert result is None

    def test_caches_result_on_second_call(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(
            _places_ok_response()
        )) as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                r1 = get_nearest_transit_station(49.28, -123.12)
                r2 = get_nearest_transit_station(49.28, -123.12)
        assert mock_open.call_count == 1
        assert r1 == r2

    def test_different_coords_are_cached_separately(self):
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(
            _places_ok_response()
        )) as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                get_nearest_transit_station(49.28, -123.12)
                get_nearest_transit_station(49.20, -122.80)
        assert mock_open.call_count == 2


# ---------------------------------------------------------------------------
# Tests: _get_distance_matrix_batch
# ---------------------------------------------------------------------------

class TestGetDistanceMatrixBatch:
    def test_returns_distance_and_duration_on_ok(self):
        origins = [(49.28, -123.12)]
        destinations = [(49.20, -123.0)]
        payload = _distance_matrix_ok_response(dist_m=5000, dur_s=600)

        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                results = _get_distance_matrix_batch(origins, destinations, "driving")

        assert len(results) == 1
        assert len(results[0]) == 1
        dist_km, dur_min = results[0][0]
        assert dist_km == pytest.approx(5.0)
        assert dur_min == pytest.approx(10.0)

    def test_returns_none_cells_on_network_error(self):
        origins = [(49.28, -123.12)]
        destinations = [(49.20, -123.0)]

        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                results = _get_distance_matrix_batch(origins, destinations, "driving")

        assert results[0][0] is None

    def test_returns_none_for_non_ok_api_status(self):
        payload = {"status": "REQUEST_DENIED", "rows": []}
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                results = _get_distance_matrix_batch([(49.28, -123.12)], [(49.20, -123.0)], "driving")

        assert results[0][0] is None

    def test_serves_cached_results_without_api_call(self):
        origins = [(49.28, -123.12)]
        destinations = [(49.20, -123.0)]
        payload = _distance_matrix_ok_response()

        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)) as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                _get_distance_matrix_batch(origins, destinations, "driving")
                results = _get_distance_matrix_batch(origins, destinations, "driving")

        assert mock_open.call_count == 1
        assert results[0][0] is not None

    def test_multiple_origins_multiple_destinations(self):
        origins = [(49.28, -123.12), (49.25, -123.10)]
        destinations = [(49.20, -123.0), (49.18, -122.9)]
        payload = {
            "status": "OK",
            "rows": [
                {
                    "elements": [
                        {"status": "OK", "distance": {"value": 5000}, "duration": {"value": 600}},
                        {"status": "OK", "distance": {"value": 8000}, "duration": {"value": 900}},
                    ]
                },
                {
                    "elements": [
                        {"status": "OK", "distance": {"value": 3000}, "duration": {"value": 400}},
                        {"status": "OK", "distance": {"value": 6000}, "duration": {"value": 700}},
                    ]
                },
            ],
        }
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                results = _get_distance_matrix_batch(origins, destinations, "driving")

        assert results[0][0] == pytest.approx((5.0, 10.0))
        assert results[0][1] == pytest.approx((8.0, 15.0))
        assert results[1][0] == pytest.approx((3.0, 400 / 60))
        assert results[1][1] == pytest.approx((6.0, 700 / 60))


# ---------------------------------------------------------------------------
# Tests: enrich_listings_with_proximity
# ---------------------------------------------------------------------------

class TestEnrichListingsWithProximity:
    def test_fixed_destination_rule_sets_proximity(self):
        listings = [_sample_listing_dict(lat=49.28, lon=-123.12)]
        rules = [ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=30)]
        geocoded = [GeocodedReference(location="Downtown Vancouver", lat=49.28, lon=-123.1, display_name="Downtown Vancouver")]

        payload = _distance_matrix_ok_response(dist_m=2000, dur_s=300)
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity(listings, rules, geocoded)

        assert len(result) == 1
        prox = result[0]["proximity"]
        key = "Downtown Vancouver|drive"
        assert key in prox
        assert prox[key]["distance_km"] == pytest.approx(2.0)
        assert prox[key]["duration_min"] == pytest.approx(5.0)

    def test_listing_without_coords_gets_none_proximity(self):
        listing = _sample_listing_dict()
        listing["latitude"] = None
        listing["longitude"] = None
        rules = [ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=30)]
        geocoded = [GeocodedReference(location="Downtown Vancouver", lat=49.28, lon=-123.1, display_name="Downtown Vancouver")]

        with patch("urllib.request.urlopen") as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity([listing], rules, geocoded)

        mock_open.assert_not_called()
        assert result[0]["proximity"]["Downtown Vancouver|drive"] is None

    def test_nearest_transit_rule_uses_places_and_directions(self):
        listings = [_sample_listing_dict(lat=49.28, lon=-123.12)]
        rules = [ProximityRule(location="nearest transit station", mode="walk", max_minutes=10)]
        geocoded = []

        places_resp = _places_ok_response(lat=49.2701, lon=-123.0, name="Station")
        directions_resp = _directions_ok_response(dist_m=800, dur_s=480)

        call_count = {"n": 0}
        def urlopen_side_effect(url, timeout=None):
            call_count["n"] += 1
            if "nearbysearch" in url:
                return _make_urlopen_response(places_resp)
            return _make_urlopen_response(directions_resp)

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity(listings, rules, geocoded)

        assert call_count["n"] >= 2
        prox = result[0]["proximity"]
        key = "nearest transit station|walk"
        assert key in prox
        assert prox[key]["duration_min"] == pytest.approx(8.0)

    def test_nearest_transit_listing_without_coords_gets_none(self):
        listing = _sample_listing_dict()
        listing["latitude"] = None
        listing["longitude"] = None
        rules = [ProximityRule(location="nearest transit station", mode="walk", max_minutes=10)]
        geocoded = []

        with patch("urllib.request.urlopen") as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity([listing], rules, geocoded)

        mock_open.assert_not_called()
        assert result[0]["proximity"]["nearest transit station|walk"] is None

    def test_mixed_fixed_and_transit_rules(self):
        listings = [_sample_listing_dict(lat=49.28, lon=-123.12)]
        rules = [
            ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=30),
            ProximityRule(location="nearest transit station", mode="walk", max_minutes=10),
        ]
        geocoded = [GeocodedReference(
            location="Downtown Vancouver", lat=49.28, lon=-123.1, display_name="Downtown Vancouver"
        )]

        matrix_resp = _distance_matrix_ok_response(dist_m=2000, dur_s=300)
        places_resp = _places_ok_response(lat=49.2701, lon=-123.0)
        directions_resp = _directions_ok_response(dist_m=800, dur_s=480)

        def urlopen_side_effect(url, timeout=None):
            if "distancematrix" in url:
                return _make_urlopen_response(matrix_resp)
            if "nearbysearch" in url:
                return _make_urlopen_response(places_resp)
            return _make_urlopen_response(directions_resp)

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity(listings, rules, geocoded)

        prox = result[0]["proximity"]
        assert "Downtown Vancouver|drive" in prox
        assert "nearest transit station|walk" in prox
        assert prox["Downtown Vancouver|drive"] is not None
        assert prox["nearest transit station|walk"] is not None

    def test_unresolvable_fixed_rule_ref_gets_none(self):
        """Rule whose location has no geocoded reference → proximity = None."""
        listings = [_sample_listing_dict(lat=49.28, lon=-123.12)]
        rules = [ProximityRule(location="Unreachable Place", mode="drive", max_minutes=30)]
        geocoded = []  # no ref for "Unreachable Place"

        with patch("urllib.request.urlopen") as mock_open:
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity(listings, rules, geocoded)

        mock_open.assert_not_called()
        assert result[0]["proximity"]["Unreachable Place|drive"] is None

    def test_accepts_listing_objects(self):
        """enrich_listings_with_proximity accepts Listing model objects as well as dicts."""
        from rental_search_agent.models import Listing
        listing = Listing(
            id="obj-1",
            title="Test Listing",
            address="100 Main St",
            url="https://example.com/1",
            bedrooms=2,
            price=2500,
            latitude=49.28,
            longitude=-123.12,
        )
        rules = [ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=30)]
        geocoded = [GeocodedReference(
            location="Downtown Vancouver", lat=49.28, lon=-123.1, display_name="Downtown Vancouver"
        )]
        payload = _distance_matrix_ok_response(dist_m=1000, dur_s=120)
        with patch("urllib.request.urlopen", return_value=_make_urlopen_response(payload)):
            with patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-key"}):
                result = enrich_listings_with_proximity([listing], rules, geocoded)

        assert result[0]["proximity"]["Downtown Vancouver|drive"]["distance_km"] == pytest.approx(1.0)

    def test_empty_listings_returns_empty(self):
        rules = [ProximityRule(location="Downtown Vancouver", mode="drive", max_minutes=30)]
        geocoded = [GeocodedReference(
            location="Downtown Vancouver", lat=49.28, lon=-123.1, display_name="Downtown Vancouver"
        )]
        result = enrich_listings_with_proximity([], rules, geocoded)
        assert result == []

    def test_empty_rules_returns_listings_with_empty_proximity(self):
        listings = [_sample_listing_dict()]
        result = enrich_listings_with_proximity(listings, [], [])
        assert len(result) == 1
        assert result[0]["proximity"] == {}
