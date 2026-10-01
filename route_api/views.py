"""POST /api/route/ — geocode, route (one OSRM call), plan fuel stops."""

from __future__ import annotations

import logging
import math

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from route_api.exceptions import ApiError, InvalidRequestError
from route_api.serializers import RouteRequestSerializer
from route_api.services import geocoding, routing
from route_api.services.fuel_data import get_fuel_data
from route_api.services.geo_utils import haversine_miles
from route_api.services.optimizer import find_route_candidates, plan_fuel_stops

logger = logging.getLogger("route_api")


class RouteView(APIView):
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # 400 with field-level errors

        start_query = serializer.validated_data["start"]
        finish_query = serializer.validated_data["finish"]

        try:
            if start_query.lower() == finish_query.lower():
                raise InvalidRequestError("Start and finish must be different locations.")

            start = geocoding.geocode(start_query)
            finish = geocoding.geocode(finish_query)

            if float(haversine_miles(start.latitude, start.longitude,
                                     finish.latitude, finish.longitude)) < 0.05:
                raise InvalidRequestError("Start and finish resolve to the same location.")

            route = routing.get_route(start.latitude, start.longitude,
                                      finish.latitude, finish.longitude)

            dataset = get_fuel_data()
            candidates = find_route_candidates(route, dataset, settings.ROUTE_CORRIDOR_MILES)
            plan = plan_fuel_stops(
                route.distance_miles,
                candidates,
                max_range=settings.VEHICLE_MAX_RANGE_MILES,
                mpg=settings.VEHICLE_MPG,
            )

            return Response(_build_response(start_query, finish_query, route, plan))
        except ApiError as exc:
            return Response({"error": exc.message}, status=exc.status)
        except Exception:
            logger.exception("Unhandled error while planning route %r -> %r", start_query, finish_query)
            return Response({"error": "Internal server error."}, status=500)


def _build_response(start_query: str, finish_query: str, route: routing.Route, plan) -> dict:
    fuel_stops = []
    for stop in plan.stops:
        gallons = round(stop.gallons, 2)
        price = round(stop.station.price, 4)
        fuel_stops.append(
            {
                "name": stop.station.name,
                "address": stop.station.address,
                "city": stop.station.city,
                "state": stop.station.state,
                "latitude": round(stop.station.latitude, 6),
                "longitude": round(stop.station.longitude, 6),
                "distance_from_start_miles": round(stop.along_miles, 1),
                "fuel_price": price,
                "gallons_purchased": gallons,
                "fuel_cost": round(gallons * price, 2),
            }
        )

    total_cost = round(sum(stop["fuel_cost"] for stop in fuel_stops), 2)
    # Same round-then-sum convention as total_cost, so the displayed stops
    # always add up to the displayed totals.
    gallons_purchased = round(sum(stop["gallons_purchased"] for stop in fuel_stops), 2)
    return {
        "start": start_query,
        "finish": finish_query,
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_minutes": round(route.duration_minutes, 1),
            "geometry": [
                [round(float(lat), 5), round(float(lon), 5)]
                for lat, lon in route.geometry.points
            ],
        },
        "fuel_stops": fuel_stops,
        "fuel": {
            "mpg": int(settings.VEHICLE_MPG),
            "max_range_miles": int(settings.VEHICLE_MAX_RANGE_MILES),
            "total_gallons": round(route.distance_miles / settings.VEHICLE_MPG, 2),
            "total_cost": total_cost,
            "gallons_purchased": gallons_purchased,
        },
    }
