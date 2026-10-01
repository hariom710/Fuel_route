"""Driving-route retrieval via OSRM.

Exactly one OSRM request per API call returns distance, duration and the full
route geometry; all fuel-station analysis happens locally on that geometry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from route_api.exceptions import RoutingFailedError
from route_api.services.geo_utils import RouteGeometry, decode_polyline

logger = logging.getLogger("route_api")

_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_minutes: float
    geometry: RouteGeometry


def get_route(start_lat: float, start_lon: float, finish_lat: float, finish_lon: float) -> Route:
    """Fetch the driving route between two points (one OSRM call)."""
    from django.conf import settings

    url = (
        f"{settings.OSRM_BASE_URL}/route/v1/driving/"
        f"{start_lon:.6f},{start_lat:.6f};{finish_lon:.6f},{finish_lat:.6f}"
    )
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "polyline", "alternatives": "false"},
            headers={"User-Agent": "fuel-route-optimizer/1.0"},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OSRM request failed: %s", exc)
        raise RoutingFailedError("Routing service is unavailable.") from exc

    routes = payload.get("routes") or []
    if payload.get("code") != "Ok" or not routes:
        logger.warning("OSRM returned no route (code=%s)", payload.get("code"))
        raise RoutingFailedError("OSRM could not find a driving route between these locations.")

    best = routes[0]
    distance_miles = float(best["distance"]) / 1609.344
    duration_minutes = float(best["duration"]) / 60.0
    try:
        geometry = RouteGeometry(decode_polyline(best["geometry"]), distance_miles)
    except (ValueError, TypeError) as exc:
        logger.warning("OSRM returned unusable geometry: %s", exc)
        raise RoutingFailedError("OSRM returned unusable route geometry.") from exc

    logger.info(
        "OSRM route: %.1f mi, %.0f min, %d geometry points",
        distance_miles, duration_minutes, len(geometry.points),
    )
    return Route(distance_miles=distance_miles, duration_minutes=duration_minutes, geometry=geometry)
