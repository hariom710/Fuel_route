"""Fuel-stop optimization over the OSRM route geometry.

Pipeline:
  1. find_route_candidates(): local, vectorized filtering of US stations to a
     corridor around the route, each mapped to an along-route mile marker.
     No external API calls are made here.
  2. plan_fuel_stops(): shortest path (Dijkstra) over the reachability graph
     where an edge exists only when the leg is within the vehicle's 500-mile
     range. Edge cost = fuel for that leg purchased at the leg's origin
     station, so the cheapest feasible combination of stops wins.

Cost model (documented in the README):
  * The vehicle departs with a full 50-gallon tank (500 miles of range); that
    initial fuel is not charged to the trip.
  * At every stop the vehicle buys exactly the gallons needed to reach the
    next stop or the destination, so each leg's fuel is paid at the station
    where the leg begins.
  * total_cost = sum of the stop costs; total consumption is route_miles / 10.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from route_api.exceptions import NoFeasibleFuelPlanError
from route_api.services.fuel_data import FuelDataset, FuelStation
from route_api.services.routing import Route

MAX_RANGE_MILES = 500.0
MPG = 10.0


@dataclass(frozen=True)
class RouteCandidate:
    station: FuelStation
    along_miles: float
    off_route_miles: float


@dataclass(frozen=True)
class PlannedStop:
    station: FuelStation
    along_miles: float
    gallons: float
    cost: float


@dataclass(frozen=True)
class FuelPlan:
    stops: tuple[PlannedStop, ...]
    total_cost: float
    purchased_gallons: float


def find_route_candidates(
    route: Route,
    dataset: FuelDataset,
    corridor_miles: float,
) -> list[RouteCandidate]:
    """Stations within `corridor_miles` of the route, sorted along the route."""
    stations = dataset.stations
    if not stations:
        return []

    geometry = route.geometry
    min_lat, max_lat, min_lon, max_lon = geometry.bounds(corridor_miles)
    lats, lons = dataset.lats, dataset.lons
    box_mask = (
        (lats >= min_lat) & (lats <= max_lat)
        & (lons >= min_lon) & (lons <= max_lon)
    )
    if not box_mask.any():
        return []

    box_indices = np.flatnonzero(box_mask)
    vertices = geometry.nearest_vertex(lats[box_mask], lons[box_mask])

    candidates: list[RouteCandidate] = []
    for offset, station_index in enumerate(box_indices):
        station = stations[station_index]
        along, perp = geometry.project(station.latitude, station.longitude, int(vertices[offset]))
        if perp > corridor_miles:
            continue
        along = min(max(along, 0.0), geometry.total_miles)
        candidates.append(RouteCandidate(station=station, along_miles=along, off_route_miles=perp))

    candidates.sort(key=lambda c: (c.along_miles, c.station.price, c.station.name))
    return candidates


def plan_fuel_stops(
    route_miles: float,
    candidates: list[RouteCandidate],
    max_range: float = MAX_RANGE_MILES,
    mpg: float = MPG,
) -> FuelPlan:
    """Minimum-cost fuel-stop plan, or NoFeasibleFuelPlanError if none exists.

    Nodes are the route start, each candidate (sorted by mile marker) and the
    destination. Because edges only point forward along the route, a single
    left-to-right relaxation pass computes the optimum deterministically;
    equal-cost paths are broken toward fewer stops.
    """
    alongs = [0.0] + [c.along_miles for c in candidates] + [route_miles]
    prices = [0.0] + [c.station.price for c in candidates] + [0.0]
    node_count = len(alongs)
    finish = node_count - 1

    inf = float("inf")
    best_cost = [inf] * node_count
    best_stops = [0] * node_count
    parent = [-1] * node_count
    best_cost[0] = 0.0

    for i in range(node_count):
        if best_cost[i] == inf:
            continue
        j = i + 1
        while j < node_count:
            leg = alongs[j] - alongs[i]
            if leg > max_range + 1e-9:
                break
            if leg > 1e-9:  # coincident nodes offer no useful edge
                cost = best_cost[i] + (leg / mpg) * prices[i]
                stops = best_stops[i] + (1 if 0 < i < finish else 0)
                if cost < best_cost[j] - 1e-9 or (
                    abs(cost - best_cost[j]) <= 1e-9 and stops < best_stops[j]
                ):
                    best_cost[j] = cost
                    best_stops[j] = stops
                    parent[j] = i
            j += 1

    if best_cost[finish] == inf:
        raise NoFeasibleFuelPlanError(
            "No feasible fuel-stop plan exists within the 500-mile vehicle range "
            "for this route (a gap between reachable stations exceeds the range)."
        )

    # Reconstruct the path, then price each stop by its outgoing leg.
    path: list[int] = []
    node = finish
    while node != -1:
        path.append(node)
        node = parent[node]
    path.reverse()

    stops: list[PlannedStop] = []
    total_cost = 0.0
    purchased_gallons = 0.0
    for position, node in enumerate(path[:-1]):
        if node == 0:
            continue  # the initial tank covers the first leg
        nxt = path[position + 1]
        gallons = (alongs[nxt] - alongs[node]) / mpg
        cost = gallons * prices[node]
        stops.append(
            PlannedStop(
                station=candidates[node - 1].station,
                along_miles=alongs[node],
                gallons=gallons,
                cost=cost,
            )
        )
        total_cost += cost
        purchased_gallons += gallons

    return FuelPlan(stops=tuple(stops), total_cost=total_cost, purchased_gallons=purchased_gallons)
