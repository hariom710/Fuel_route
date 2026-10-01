"""Shared fixtures and offline HTTP mocks for the test suite.

Every external call (Photon geocoding, OSRM routing) is served from these
helpers; no test ever touches the network. The fuel CSV and city-coordinate
CSV are local fixtures under tests/fixtures/.

Synthetic route layout used across tests:

    * A route is a straight line of constant latitude running east from
      longitude -105 to -85 (20 degrees), scaled to the requested OSRM
      distance, so a station's longitude maps linearly to its mile marker:
          mile = (lon + 105) / 20 * distance_miles
    * Line A (lat 40.0): the main station set (Alpha..India every 100 miles)
      plus the off-corridor station Zulu at lat 42.0.
    * Lines B/C/D (lat 41.5 / 42.5 / 43.5): dedicated station sets for the
      range-constraint scenarios. Lines are more than 25 corridor miles apart,
      so each test sees only its own stations.
"""

from __future__ import annotations

from pathlib import Path

import requests

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FUEL_CSV = FIXTURES_DIR / "fuel_prices.csv"
CITY_COORDS_CSV = FIXTURES_DIR / "city_coordinates.csv"

START_QUERY = "Start City, CO"
FINISH_QUERY = "End City, NJ"

METERS_PER_MILE = 1609.344


# --------------------------------------------------------------------------
# Polyline codec (the encoder mirrors OSRM's geometries=polyline output and
# round-trips against route_api.services.geo_utils.decode_polyline).
# --------------------------------------------------------------------------

def encode_polyline(points: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    output: list[str] = []
    prev_lat = 0
    prev_lon = 0
    for lat, lon in points:
        ilat = round(lat * factor)
        ilon = round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else (delta << 1)
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(output)


def route_points(lat: float, lon_start: float = -105.0, lon_end: float = -85.0,
                 step: float = 0.05) -> list[tuple[float, float]]:
    """Straight east-west polyline; longitude is linear in along-route miles."""
    count = int(round((lon_end - lon_start) / step))
    return [(lat, round(lon_start + i * step, 5)) for i in range(count + 1)]


def lon_for_mile(mile: float, distance_miles: float = 1000.0,
                 lon_start: float = -105.0, lon_end: float = -85.0) -> float:
    return round(lon_start + (mile / distance_miles) * (lon_end - lon_start), 5)


# --------------------------------------------------------------------------
# Fake HTTP layer
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:  # all mocked responses are 200
        return None

    def json(self) -> dict:
        return self._payload


def photon_payload(query: str, locations: dict[str, tuple[float, float]],
                   foreign_queries: dict[str, tuple[float, float]],
                   unknown_queries: set[str]) -> dict:
    """Photon-style body: geometry.coordinates is [lon, lat]."""
    if query in unknown_queries:
        return {"features": []}
    if query in foreign_queries:
        lon, lat = foreign_queries[query]
        return {"features": [{"type": "Feature",
                              "geometry": {"type": "Point", "coordinates": [lon, lat]},
                              "properties": {"name": query, "country": "Canada",
                                             "countrycode": "CA"}}]}
    if query in locations:
        lon, lat = locations[query]
        return {"features": [{"type": "Feature",
                              "geometry": {"type": "Point", "coordinates": [lon, lat]},
                              "properties": {"name": query, "state": "Colorado",
                                             "country": "United States",
                                             "countrycode": "US"}}]}
    return {"features": []}


def osrm_payload(points: list[tuple[float, float]], distance_m: float,
                 duration_s: float) -> dict:
    return {"code": "Ok", "routes": [{
        "distance": distance_m,
        "duration": duration_s,
        "geometry": encode_polyline(points),
    }]}


def make_requests_get(*, points: list[tuple[float, float]], distance_mi: float,
                      duration_min: float,
                      locations: dict[str, tuple[float, float]],
                      foreign_queries: dict[str, tuple[float, float]] | None = None,
                      unknown_queries: set[str] | None = None,
                      osrm_error: Exception | None = None,
                      photon_error: Exception | None = None):
    """A requests.get replacement dispatching Photon/OSRM by URL.

    Returns the callable with an `osrm_calls` counter attached so tests can
    assert the one-OSRM-request budget.
    """
    foreign_queries = foreign_queries or {}
    unknown_queries = unknown_queries or set()

    def fake_get(url, **kwargs):
        if "/route/v1/driving/" in url:
            fake_get.osrm_calls += 1
            if osrm_error is not None:
                raise osrm_error
            return FakeResponse(osrm_payload(points, distance_mi * METERS_PER_MILE,
                                             duration_min * 60.0))
        query = (kwargs.get("params") or {}).get("q", "")
        if photon_error is not None:
            raise photon_error
        return FakeResponse(photon_payload(query, locations, foreign_queries,
                                           unknown_queries))

    fake_get.osrm_calls = 0
    return fake_get


def us_locations(lat: float) -> dict[str, tuple[float, float]]:
    """Geocoded start/finish on the given route line."""
    return {START_QUERY: (-105.0, lat), FINISH_QUERY: (-85.0, lat)}
