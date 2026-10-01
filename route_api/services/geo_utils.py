"""Geographic helpers: normalization, distances, polyline decoding, route projection."""

from __future__ import annotations

import math
import re
import unicodedata

import numpy as np

EARTH_RADIUS_MILES = 3958.7613

US_STATE_ABBREVIATIONS = frozenset(
    """
    AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT
    NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC
    """.split()
)


def us_state_abbreviations() -> frozenset[str]:
    return US_STATE_ABBREVIATIONS


def normalize_city(value: str) -> str:
    """Lowercase, strip accents/punctuation so 'Mc Donald' == 'mcdonald', 'Dover-Foxcroft' == 'dover foxcroft'."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\bsainte?\b", "st", text.lower())  # Saint Louis == St. Louis
    return re.sub(r"[^a-z0-9]", "", text)


def normalize_state(value: str) -> str:
    return str(value or "").strip().upper()


def haversine_miles(lat1: np.ndarray | float, lon1: np.ndarray | float,
                    lat2: np.ndarray | float, lon2: np.ndarray | float) -> np.ndarray | float:
    """Great-circle distance in miles; broadcasts over numpy arrays."""
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(np.asarray(lat2) - np.asarray(lat1))
    dlam = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode a Google-style encoded polyline into (latitude, longitude) pairs."""
    coordinates: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    factor = 10 ** precision
    while index < len(encoded):
        for coord in (0, 1):  # lat then lon
            result = 0
            shift_index = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift_index
                shift_index += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if coord == 0:
                lat += delta
            else:
                lon += delta
        coordinates.append((lat / factor, lon / factor))
    return coordinates


def cumulative_miles(points: np.ndarray) -> np.ndarray:
    """Cumulative great-circle distance along a (N, 2) array of (lat, lon) points."""
    if len(points) < 2:
        return np.zeros(len(points))
    segment = haversine_miles(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])
    return np.concatenate(([0.0], np.cumsum(segment)))


def project_point_to_route(lat: float, lon: float, points: np.ndarray,
                           cum: np.ndarray, vertex_index: int, scale: float = 1.0) -> tuple[float, float]:
    """Refine a station's nearest vertex onto adjacent segments.

    Returns (along_route_miles, perpendicular_miles) using a local
    equirectangular frame, which is accurate at these scales. `cum` is assumed
    to be already scaled to the routing engine's reported distance; `scale`
    converts raw segment lengths to the same scale.
    """
    best_along = float(cum[vertex_index])
    best_perp = float(haversine_miles(lat, lon, points[vertex_index, 0], points[vertex_index, 1]))

    for seg in range(max(0, vertex_index - 1), min(len(points) - 1, vertex_index + 1)):
        lat0, lon0 = points[seg]
        lat1, lon1 = points[seg + 1]
        mid_lat = math.radians((lat0 + lat1) / 2.0)
        # Local miles-per-degree frame around the segment.
        x = math.radians(lon - lon0) * math.cos(mid_lat) * EARTH_RADIUS_MILES
        y = math.radians(lat - lat0) * EARTH_RADIUS_MILES
        dx = math.radians(lon1 - lon0) * math.cos(mid_lat) * EARTH_RADIUS_MILES
        dy = math.radians(lat1 - lat0) * EARTH_RADIUS_MILES
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            continue
        t = max(0.0, min(1.0, (x * dx + y * dy) / seg_len_sq))
        perp = math.hypot(x - t * dx, y - t * dy)
        if perp < best_perp:
            best_perp = perp
            best_along = float(cum[seg] + t * math.sqrt(seg_len_sq) * scale)
    return best_along, best_perp


class RouteGeometry:
    """Decoded OSRM geometry with cumulative mile markers scaled to OSRM's distance."""

    def __init__(self, coordinates: list[tuple[float, float]], distance_miles: float):
        if len(coordinates) < 2:
            raise ValueError("Route geometry must contain at least two points.")
        self.points = np.asarray(coordinates, dtype=float)
        cum = cumulative_miles(self.points)
        raw_total = float(cum[-1])
        self.scale = distance_miles / raw_total if raw_total > 0 else 1.0
        self.cum = cum * self.scale
        self.total_miles = distance_miles

    def bounds(self, pad_miles: float) -> tuple[float, float, float, float]:
        """(min_lat, max_lat, min_lon, max_lon) expanded by pad_miles."""
        pad_lat = pad_miles / 69.0
        max_abs_lat = float(np.max(np.abs(self.points[:, 0])))
        pad_lon = pad_miles / max(69.0 * math.cos(math.radians(max_abs_lat)), 1e-6)
        return (
            float(self.points[:, 0].min()) - pad_lat,
            float(self.points[:, 0].max()) + pad_lat,
            float(self.points[:, 1].min()) - pad_lon,
            float(self.points[:, 1].max()) + pad_lon,
        )

    def nearest_vertex(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Index of the nearest route vertex for each station (vectorized)."""
        idx = np.empty(len(lat), dtype=int)
        # Chunk to bound the (chunk x N_points) distance matrix: for a 35k-point
        # cross-country route one temporary is ~18 MB at chunk 64 (~72 MB at
        # the previous chunk size of 256), keeping peak memory roughly 4x lower.
        for start in range(0, len(lat), 64):
            stop = min(start + 64, len(lat))
            dist = haversine_miles(
                lat[start:stop, None], lon[start:stop, None],
                self.points[None, :, 0], self.points[None, :, 1],
            )
            idx[start:stop] = np.argmin(dist, axis=1)
        return idx

    def project(self, lat: float, lon: float, vertex_index: int) -> tuple[float, float]:
        """(along-route miles, perpendicular miles) for one point."""
        return project_point_to_route(lat, lon, self.points, self.cum, vertex_index, self.scale)
