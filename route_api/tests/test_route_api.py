"""End-to-end tests for POST /api/route/.

All external services (Photon geocoding, OSRM routing) are mocked in-process;
the fuel CSV and city coordinates come from local fixtures. See tests/utils.py
for the synthetic route layout.

Expected optimizer outcome on the main 1,000-mile line-A route (verified
against the documented cost model in route_api/services/optimizer.py):

    Single-stop option:     stop at mile 500             -> 500 mi / 10 * 3.50 = 175.00
    Chosen optimal plan:    stop at mile 500 (100 mi out) + stop at mile 600
                            (400 mi out)                  -> 10*3.50 + 40*3.20 = 163.00
    Fuel consumed:          1000 / 10                     = 100.00 gallons
    Fuel purchased:         1000 - 500                    =  50.00 gallons
"""

from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from route_api.services import fuel_data, geocoding, routing
from route_api.services.optimizer import find_route_candidates
from route_api.tests.utils import (
    CITY_COORDS_CSV,
    FUEL_CSV,
    FINISH_QUERY,
    START_QUERY,
    make_requests_get,
    route_points,
    us_locations,
)

FOREIGN_QUERY = "Toronto, ON, Canada"
UNKNOWN_QUERY = "Unknown Place, ZZ"

REQUIRED_STOP_FIELDS = {
    "name", "address", "city", "state", "latitude", "longitude",
    "distance_from_start_miles", "fuel_price", "gallons_purchased", "fuel_cost",
}


class RouteEndpointTestCase(SimpleTestCase):
    def setUp(self):
        geocoding.clear_cache()
        fuel_data.reset_cache()
        overrides = override_settings(
            FUEL_CSV_PATH=FUEL_CSV,
            CITY_COORDS_PATH=CITY_COORDS_CSV,
            ROUTE_CORRIDOR_MILES=25.0,
            VEHICLE_MAX_RANGE_MILES=500.0,
            VEHICLE_MPG=10.0,
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.client = APIClient()

    # -- helpers ------------------------------------------------------------

    def mock_http(self, fake_get):
        patcher = patch("requests.get", side_effect=fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def line_route(self, lat, distance_mi=1000.0, duration_min=600.0,
                   lon_end=-85.0, **kwargs):
        return make_requests_get(
            points=route_points(lat, lon_end=lon_end),
            distance_mi=distance_mi,
            duration_min=duration_min,
            locations=us_locations(lat),
            unknown_queries={UNKNOWN_QUERY},
            foreign_queries={FOREIGN_QUERY: (-79.4, 43.7)},
            **kwargs,
        )

    def post_route(self, body, fake_get):
        self.mock_http(fake_get)
        return self.client.post("/api/route/", body, format="json")

    # -- required happy-path / validation tests -----------------------------

    def test_valid_request_returns_full_plan(self):
        """200: route + geometry + price-optimized stops + fuel breakdown."""
        fake = self.line_route(40.0)
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY}, fake)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        route = data["route"]
        self.assertEqual(route["distance_miles"], 1000.0)
        self.assertEqual(route["duration_minutes"], 600.0)
        self.assertGreater(len(route["geometry"]), 100)
        self.assertAlmostEqual(route["geometry"][0][0], 40.0, places=3)

        stops = data["fuel_stops"]
        self.assertEqual([stop["name"] for stop in stops],
                         ["Echo Station", "Foxtrot Station"])
        for stop in stops:
            self.assertTrue(REQUIRED_STOP_FIELDS.issubset(stop), stop)

        fuel = data["fuel"]
        self.assertEqual(fuel["mpg"], 10)
        self.assertEqual(fuel["max_range_miles"], 500)
        self.assertEqual(fuel["total_gallons"], 100.0)
        self.assertEqual(fuel["total_cost"], 163.0)
        self.assertEqual(fuel["gallons_purchased"], 50.0)

        # Exactly one OSRM request per API call.
        self.assertEqual(fake.osrm_calls, 1)

    def test_missing_start_returns_400(self):
        response = self.post_route({"finish": FINISH_QUERY}, self.line_route(40.0))
        self.assertEqual(response.status_code, 400)
        self.assertIn("start", response.json())

    def test_missing_finish_returns_400(self):
        response = self.post_route({"start": START_QUERY}, self.line_route(40.0))
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json())

    def test_invalid_location_returns_400(self):
        response = self.post_route({"start": UNKNOWN_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(40.0))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not resolve location", response.json()["error"])

    def test_non_usa_location_returns_400(self):
        response = self.post_route({"start": FOREIGN_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(40.0))
        self.assertEqual(response.status_code, 400)
        self.assertIn("outside the USA", response.json()["error"])

    def test_short_route_returns_no_fuel_stops(self):
        """A 300-mile route fits in one tank: no stops, zero fuel cost."""
        fake = self.line_route(40.0, distance_mi=300.0, duration_min=300.0,
                               lon_end=-100.0)
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY}, fake)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["route"]["distance_miles"], 300.0)
        self.assertEqual(data["fuel_stops"], [])
        self.assertEqual(data["fuel"]["total_cost"], 0.0)
        self.assertEqual(data["fuel"]["gallons_purchased"], 0.0)
        self.assertEqual(data["fuel"]["total_gallons"], 30.0)

    def test_long_route_returns_multiple_stops(self):
        """1,000-mile route needs two stops; every leg stays within 500 miles."""
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(40.0))
        self.assertEqual(response.status_code, 200)
        data = response.json()

        stops = data["fuel_stops"]
        self.assertGreaterEqual(len(stops), 2)
        self.assertEqual([stop["name"] for stop in stops],
                         ["Echo Station", "Foxtrot Station"])
        self.assertEqual([stop["distance_from_start_miles"] for stop in stops],
                         [500.0, 600.0])

        # Legs: start -> stop1 -> stop2 -> finish, all within vehicle range.
        legs = ([0.0] + [stop["distance_from_start_miles"] for stop in stops]
                + [data["route"]["distance_miles"]])
        for from_mile, to_mile in zip(legs, legs[1:]):
            self.assertLessEqual(to_mile - from_mile, 500.001)

    def test_station_outside_corridor_is_ignored(self):
        """Zulu (cheapest in the dataset, 138 miles off-route) never appears."""
        fake = self.line_route(40.0)
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY}, fake)
        self.assertEqual(response.status_code, 200)
        names = [stop["name"] for stop in response.json()["fuel_stops"]]
        self.assertNotIn("Zulu Station", names)

        # Corridor filtering itself: only the nine on-line stations qualify.
        self.mock_http(self.line_route(40.0))
        route = routing.get_route(40.0, -105.0, 40.0, -85.0)
        candidates = find_route_candidates(route, fuel_data.get_fuel_data(), 25.0)
        self.assertEqual(
            [c.station.name for c in candidates],
            ["Alpha Station", "Bravo Station", "Charlie Station", "Delta Station",
             "Echo Station", "Foxtrot Station", "Golf Station", "Hotel Station",
             "India Station"],
        )

    def test_stations_beyond_vehicle_range_not_selected(self):
        """First candidate at mile 600: >500 miles from start -> no feasible
        plan (422); those stations are never selected as stops."""
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(41.5))
        self.assertEqual(response.status_code, 422)
        self.assertIn("No feasible fuel-stop plan", response.json()["error"])

    def test_globally_cheapest_unreachable_station_not_selected(self):
        """November (2.400, cheapest in the dataset) sits alone behind a
        520-mile gap: unreachable -> 422, never part of a plan."""
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(42.5))
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertIn("No feasible fuel-stop plan", error)
        self.assertNotIn("November Station", str(response.json()))

    def test_destination_reachability_is_enforced(self):
        """Stations stop at mile 300 of a 1,000-mile route: the destination
        would be stranded (700-mile final leg) -> 422."""
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(43.5))
        self.assertEqual(response.status_code, 422)
        self.assertIn("No feasible fuel-stop plan", response.json()["error"])

    def test_total_gallons_matches_route_distance(self):
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(40.0))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        expected = round(data["route"]["distance_miles"] / 10, 2)
        self.assertEqual(data["fuel"]["total_gallons"], expected)

    def test_total_cost_matches_sum_of_stop_costs(self):
        """Stop-level costs must sum to total_cost (and match gallons x price)."""
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                   self.line_route(40.0))
        self.assertEqual(response.status_code, 200)
        data = response.json()

        for stop in data["fuel_stops"]:
            self.assertAlmostEqual(stop["fuel_cost"],
                                   round(stop["gallons_purchased"] * stop["fuel_price"], 2),
                                   places=2)
        summed = round(sum(stop["fuel_cost"] for stop in data["fuel_stops"]), 2)
        self.assertEqual(data["fuel"]["total_cost"], summed)
        self.assertEqual(data["fuel"]["total_cost"], 163.0)

        # Displayed stop gallons must also sum to the displayed total.
        summed_gallons = round(sum(stop["gallons_purchased"] for stop in data["fuel_stops"]), 2)
        self.assertEqual(data["fuel"]["gallons_purchased"], summed_gallons)

        # Hand-checked plan: Echo buys 10 gal @ 3.500, Foxtrot buys 40 gal @ 3.200.
        echo, foxtrot = data["fuel_stops"]
        self.assertEqual((echo["gallons_purchased"], echo["fuel_price"],
                          echo["fuel_cost"]), (10.0, 3.5, 35.0))
        self.assertEqual((foxtrot["gallons_purchased"], foxtrot["fuel_price"],
                          foxtrot["fuel_cost"]), (40.0, 3.2, 128.0))

    def test_routing_failure_returns_502(self):
        fake = self.line_route(40.0, osrm_error=requests.ConnectionError("OSRM down"))
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY}, fake)
        self.assertEqual(response.status_code, 502)
        self.assertIn("Routing service is unavailable", response.json()["error"])

    # -- additional error-path coverage -------------------------------------

    def test_geocoding_service_failure_returns_502(self):
        fake = self.line_route(40.0, photon_error=requests.ConnectionError("Photon down"))
        response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY}, fake)
        self.assertEqual(response.status_code, 502)
        self.assertIn("Geocoding service is unavailable", response.json()["error"])

    def test_same_start_and_finish_returns_400(self):
        response = self.post_route({"start": START_QUERY, "finish": START_QUERY},
                                   self.line_route(40.0))
        self.assertEqual(response.status_code, 400)
        self.assertIn("different", response.json()["error"])

    def test_missing_csv_returns_500_json_error(self):
        from pathlib import Path
        with override_settings(FUEL_CSV_PATH=Path(FUEL_CSV).parent / "nope.csv"):
            response = self.post_route({"start": START_QUERY, "finish": FINISH_QUERY},
                                       self.line_route(40.0))
        self.assertEqual(response.status_code, 500)
        self.assertIn("not found", response.json()["error"])

    def test_invalid_field_types_return_400(self):
        response = self.client.post(
            "/api/route/",
            {"start": ["not", "a", "string"], "finish": {"nested": True}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("start", body)
        self.assertIn("finish", body)
