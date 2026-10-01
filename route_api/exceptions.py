"""Application-level exceptions mapped to HTTP status codes in the view layer."""

from __future__ import annotations


class ApiError(Exception):
    status = 500
    default_message = "Unexpected error."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class InvalidRequestError(ApiError):
    status = 400
    default_message = "Invalid request."


class LocationNotFoundError(ApiError):
    status = 400
    default_message = "Location could not be resolved."


class OutsideUSAError(ApiError):
    status = 400
    default_message = "Start and finish locations must be within the USA."


class GeocodingFailedError(ApiError):
    status = 502
    default_message = "Geocoding service is unavailable."


class RoutingFailedError(ApiError):
    status = 502
    default_message = "Routing service is unavailable."


class NoFeasibleFuelPlanError(ApiError):
    status = 422
    default_message = "No feasible fuel-stop plan exists within the 500-mile vehicle range."


class FuelDataError(ApiError):
    status = 500
    default_message = "Fuel price data could not be loaded."
