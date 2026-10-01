"""Unit tests for geographic helpers (no I/O)."""

from django.test import SimpleTestCase

from route_api.services.geo_utils import decode_polyline, normalize_city, normalize_state
from route_api.tests.utils import encode_polyline


class GeoUtilsTestCase(SimpleTestCase):
    def test_polyline_roundtrip(self):
        points = [(40.0, -105.0), (40.05, -104.95), (41.5, -92.6), (43.5, -85.0)]
        encoded = encode_polyline(points)
        self.assertEqual(decode_polyline(encoded), points)

    def test_polyline_roundtrip_dense_track(self):
        points = [(40.0 + 0.001 * i, -105.0 + 0.01 * i) for i in range(200)]
        encoded = encode_polyline(points)
        self.assertEqual(decode_polyline(encoded), points)

    def test_normalize_city_equivalences(self):
        self.assertEqual(normalize_city("St. Louis"), normalize_city("Saint Louis"))
        self.assertEqual(normalize_city("Mc Donald"), normalize_city("MCDONALD"))
        self.assertEqual(normalize_city("Dover-Foxcroft"), normalize_city("dover foxcroft"))
        self.assertEqual(normalize_city("  Canon City "), normalize_city("Cañon City"))
        self.assertEqual(normalize_city(""), "")

    def test_normalize_state(self):
        self.assertEqual(normalize_state(" co "), "CO")
        self.assertEqual(normalize_state(None), "")
