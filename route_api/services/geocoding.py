"""Start/finish geocoding via Photon (free, keyless, no usage registration).

Results are cached in-process so repeated requests for the same location
make no external calls. Every resolved location is validated as US.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from route_api.exceptions import (
    GeocodingFailedError,
    LocationNotFoundError,
    OutsideUSAError,
)

logger = logging.getLogger("route_api")

_CACHE: dict[str, "GeocodedLocation"] = {}
_US_COUNTRIES = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class GeocodedLocation:
    query: str
    label: str
    latitude: float
    longitude: float
    country_code: str


def geocode(query: str) -> GeocodedLocation:
    """Resolve a free-form location string to coordinates, requiring a US result."""
    key = query.strip().lower()
    if key in _CACHE:
        return _CACHE[key]

    try:
        response = requests.get(
            f"{_base_url()}/api/",
            params={"q": query.strip(), "limit": 1},
            headers={"User-Agent": "fuel-route-optimizer/1.0"},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Photon geocoding failed for %r: %s", query, exc)
        raise GeocodingFailedError() from exc

    if not features:
        raise LocationNotFoundError(f"Could not resolve location '{query}'.")

    properties = features[0].get("properties", {})
    coordinates = features[0].get("geometry", {}).get("coordinates", [])
    if len(coordinates) != 2:
        raise LocationNotFoundError(f"Could not resolve location '{query}'.")

    country_code = str(properties.get("countrycode", "")).upper()
    if country_code not in _US_COUNTRIES:
        raise OutsideUSAError(
            f"Location '{query}' resolves to "
            f"'{properties.get('country', 'unknown')}', outside the USA."
        )

    location = GeocodedLocation(
        query=query.strip(),
        label=", ".join(
            part for part in (
                properties.get("name"),
                properties.get("state"),
                properties.get("country"),
            ) if part
        ),
        latitude=float(coordinates[1]),
        longitude=float(coordinates[0]),
        country_code=country_code,
    )
    _CACHE[key] = location
    logger.info("Geocoded %r -> %.5f, %.5f (US)", query, location.latitude, location.longitude)
    return location


def clear_cache() -> None:
    """Used by tests."""
    _CACHE.clear()


def _base_url() -> str:
    from django.conf import settings

    return settings.PHOTON_BASE_URL
