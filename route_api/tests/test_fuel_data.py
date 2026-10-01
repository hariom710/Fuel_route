"""CSV loading behavior: cleaning, US filtering, coordinate join, stats,
load-once semantics — the data-loading contract required of the assessment.
"""

from django.test import SimpleTestCase, override_settings

from route_api.exceptions import FuelDataError
from route_api.services import fuel_data
from route_api.tests.utils import CITY_COORDS_CSV, FUEL_CSV


class FuelDataLoadingTestCase(SimpleTestCase):
    def setUp(self):
        fuel_data.reset_cache()
        self.addCleanup(fuel_data.reset_cache)
        overrides = override_settings(FUEL_CSV_PATH=FUEL_CSV,
                                      CITY_COORDS_PATH=CITY_COORDS_CSV)
        overrides.enable()
        self.addCleanup(overrides.disable)

    def test_csv_loading_reports_stats(self):
        """Loading reports total/US/non-US rows, unique pairs, matched,
        unmatched — measured on the fixture CSV (20 rows)."""
        stats = fuel_data.load_fuel_data().stats
        self.assertEqual(stats["total_rows"], 20)
        self.assertEqual(stats["invalid_rows"], 0)
        self.assertEqual(stats["us_rows"], 19)
        self.assertEqual(stats["non_us_rows"], 1)
        self.assertEqual(stats["unique_city_state_pairs"], 18)
        self.assertEqual(stats["matched_pairs"], 17)
        self.assertEqual(stats["unmatched_pairs"], 1)
        self.assertEqual(stats["excluded_unmatched_rows"], 1)
        self.assertEqual(stats["station_count"], 17)

    def test_stats_are_logged_at_load_time(self):
        with self.assertLogs("route_api", level="INFO") as captured:
            fuel_data.load_fuel_data()
        self.assertTrue(any("Fuel data loaded" in line for line in captured.output))

    def test_non_us_and_unmatched_rows_excluded(self):
        """Canadian rows and cities without GeoNames coordinates never load;
        coordinates are never invented for them."""
        dataset = fuel_data.load_fuel_data()
        pairs = {(station.city, station.state) for station in dataset.stations}
        names = {station.name for station in dataset.stations}
        self.assertNotIn(("Maple City", "AB"), pairs)       # non-US row
        self.assertNotIn("Maple Station", names)
        self.assertNotIn(("Unmatched City", "WY"), pairs)   # no coordinates
        self.assertNotIn("Mystery Station", names)
        self.assertEqual({station.state for station in dataset.stations} - {"CO"}, set())

    def test_whitespace_cleaned_and_duplicates_deduplicated(self):
        dataset = fuel_data.load_fuel_data()
        names = [station.name for station in dataset.stations]
        self.assertIn("Bravo Station", names)               # surrounding spaces stripped
        self.assertTrue(all(name == name.strip() for name in names))
        alphas = [s for s in dataset.stations if s.name == "Alpha Station"]
        self.assertEqual(len(alphas), 1)                    # duplicate CSV rows collapse
        self.assertEqual(alphas[0].price, 3.450)            # cheapest listing wins

    def test_fuel_data_loaded_once_per_process(self):
        first = fuel_data.get_fuel_data()
        second = fuel_data.get_fuel_data()
        self.assertIs(first, second)

    def test_missing_csv_raises_fuel_data_error(self):
        with self.assertRaises(FuelDataError):
            fuel_data.load_fuel_data(fuel_csv_path="does-not-exist.csv")

    def test_missing_coordinates_csv_raises_fuel_data_error(self):
        with self.assertRaises(FuelDataError):
            fuel_data.load_fuel_data(city_coords_path="does-not-exist.csv")

    def test_csv_missing_required_columns_raises_fuel_data_error(self):
        """A structurally invalid CSV fails with a clear FuelDataError naming
        the missing columns — never an internal error."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", prefix="fuel_test_", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("Truckstop Name,City,State\nSome Station,Boulder,CO\n")
            temp_path = Path(handle.name)
        self.addCleanup(temp_path.unlink, missing_ok=True)

        with self.assertRaises(FuelDataError) as ctx:
            fuel_data.load_fuel_data(fuel_csv_path=temp_path)
        self.assertIn("missing required columns", str(ctx.exception))
