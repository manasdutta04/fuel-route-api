"""
Given a route (as returned by the OSRM client) and the pre-geocoded fuel
stations already sitting in our database, work out:

  * which stations lie close enough to the route to be usable,
  * where along the route (in miles from the start) each one sits,
  * a cheapest-first sequence of fuel stops that never lets the vehicle
    run further than VEHICLE_RANGE_MILES on a tank, and
  * the total estimated cost of fuel for the whole trip.

Everything in this module is pure local computation (haversine distance +
a KD-tree for the nearest-neighbour lookups) -- no network calls happen
here, which is what lets the API stay fast and keeps us well inside the
"one to three calls to the map/routing API" budget: those calls are spent
entirely on geocoding + the single OSRM route call, not on this step.
"""
import math
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from scipy.spatial import cKDTree

from routing.models import FuelStation

EARTH_RADIUS_MILES = 3958.8


@dataclass
class FuelStop:
    station: FuelStation
    mile_marker: float          # distance from trip start, in miles
    distance_off_route: float   # how far the station sits from the route, in miles
    gallons_purchased: float
    cost: float


def _haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _sample_route(geometry, interval_miles):
    """
    Walk the OSRM polyline and produce evenly spaced (lat, lon, cumulative_mile)
    samples. The raw polyline is dense near turns and sparse on straightaways,
    so we resample it to a consistent spacing before doing nearest-neighbour
    lookups against fuel stations.
    """
    samples = [(geometry[0][0], geometry[0][1], 0.0)]
    cumulative = 0.0
    next_target = interval_miles

    for (lat1, lon1), (lat2, lon2) in zip(geometry, geometry[1:]):
        seg_len = _haversine_miles(lat1, lon1, lat2, lon2)
        if seg_len == 0:
            continue
        seg_start_cum = cumulative
        seg_end_cum = cumulative + seg_len

        while next_target <= seg_end_cum:
            frac = (next_target - seg_start_cum) / seg_len
            lat = lat1 + frac * (lat2 - lat1)
            lon = lon1 + frac * (lon2 - lon1)
            samples.append((lat, lon, next_target))
            next_target += interval_miles

        cumulative = seg_end_cum

    samples.append((geometry[-1][0], geometry[-1][1], cumulative))
    return samples, cumulative


def _project(lat, lon, ref_lat_rad):
    """Cheap equirectangular projection to (x, y) miles, good enough over a
    radius of a few miles which is all we use it for (nearest-sample lookups,
    not long-distance measurement - that's done with haversine)."""
    x = lon * math.cos(ref_lat_rad) * 69.17
    y = lat * 69.0
    return x, y


def find_fuel_stops(route, vehicle_range_miles=None, mpg=None, search_radius_miles=None):
    """
    route: dict from routing.services.osrm_client.get_route()
    Returns: {
        'total_distance_miles': float,
        'stops': [FuelStop, ...],
        'total_fuel_cost': float,
        'feasible': bool,
        'message': str | None,
    }
    """
    vehicle_range_miles = vehicle_range_miles or settings.VEHICLE_RANGE_MILES
    mpg = mpg or settings.VEHICLE_MPG
    search_radius_miles = search_radius_miles or settings.STATION_SEARCH_RADIUS_MILES

    geometry = route['geometry']
    total_distance = route['distance_miles']

    samples, _ = _sample_route(geometry, settings.ROUTE_SAMPLE_INTERVAL_MILES)
    ref_lat_rad = math.radians(samples[len(samples) // 2][0])

    sample_points_xy = np.array([_project(lat, lon, ref_lat_rad) for lat, lon, _ in samples])
    sample_miles = np.array([m for _, _, m in samples])
    tree = cKDTree(sample_points_xy)

    stations = list(
        FuelStation.objects.filter(
            latitude__isnull=False, longitude__isnull=False,
        ).values('id', 'name', 'city', 'state', 'address', 'retail_price', 'latitude', 'longitude')
    )
    if not stations:
        return {
            'total_distance_miles': round(total_distance, 1),
            'stops': [],
            'total_fuel_cost': None,
            'feasible': False,
            'message': (
                'No fuel stations are loaded in the database yet. Run '
                '`python manage.py import_fuel_prices data/fuel-prices.csv` first.'
            ),
        }

    station_xy = np.array([_project(s['latitude'], s['longitude'], ref_lat_rad) for s in stations])
    nearest_dist_xy, nearest_idx = tree.query(station_xy)

    candidates = []  # (mile_marker, station_dict, off_route_miles)
    for station, xy_dist, idx in zip(stations, nearest_dist_xy, nearest_idx):
        sample_lat, sample_lon, mile_marker = samples[idx]
        off_route_miles = _haversine_miles(station['latitude'], station['longitude'], sample_lat, sample_lon)
        if off_route_miles <= search_radius_miles:
            candidates.append((mile_marker, station, off_route_miles))

    candidates.sort(key=lambda c: c[0])

    if total_distance <= vehicle_range_miles:
        # No refuelling needed en route - the whole trip fits on one tank.
        # We still report the single cheapest station near the corridor so
        # the caller has a concrete price to plan around.
        cheapest = min(candidates, key=lambda c: c[1]['retail_price']) if candidates else None
        gallons = total_distance / mpg
        if cheapest:
            price = float(cheapest[1]['retail_price'])
            stop = FuelStop(
                station=cheapest[1], mile_marker=round(cheapest[0], 1),
                distance_off_route=round(cheapest[2], 2),
                gallons_purchased=round(gallons, 2), cost=round(gallons * price, 2),
            )
            return {
                'total_distance_miles': round(total_distance, 1),
                'stops': [stop],
                'total_fuel_cost': round(gallons * price, 2),
                'feasible': True,
                'message': 'Whole trip fits within one tank; showing the cheapest station on the route for reference.',
            }
        return {
            'total_distance_miles': round(total_distance, 1),
            'stops': [],
            'total_fuel_cost': None,
            'feasible': True,
            'message': 'Whole trip fits within one tank and no priced station was found near the route.',
        }

    # Greedy selection: repeatedly pick the cheapest station reachable within
    # the current range window, tie-breaking on the furthest-along station so
    # we bank as much slack as possible for the next leg. This is the classic
    # greedy strategy for the "gas station problem" - optimal for minimizing
    # cost when tanks are assumed to be topped up at each stop.
    stops = []
    position = 0.0
    remaining_candidates = candidates

    while total_distance - position > vehicle_range_miles:
        window = [c for c in remaining_candidates if position < c[0] <= position + vehicle_range_miles]
        if not window:
            return {
                'total_distance_miles': round(total_distance, 1),
                'stops': [_to_fuel_stop(s) for s in stops],
                'total_fuel_cost': None,
                'feasible': False,
                'message': (
                    f'No priced fuel station found within range between mile {position:.0f} '
                    f'and mile {position + vehicle_range_miles:.0f}. Route is not feasible '
                    f'with this data set and a {vehicle_range_miles}-mile range.'
                ),
            }
        best = min(window, key=lambda c: (float(c[1]['retail_price']), -c[0]))
        stops.append(best)
        position = best[0]
        remaining_candidates = [c for c in remaining_candidates if c[0] > position]

    # Cost: each stop pays for the leg *following* it (up to the next stop, or
    # the destination for the last stop). The very first leg, from the start
    # to the first stop, is assumed to run on fuel the vehicle already had
    # before the trip began, so it isn't charged here.
    total_cost = 0.0
    fuel_stops = []
    for i, (mile_marker, station, off_route) in enumerate(stops):
        leg_end = stops[i + 1][0] if i + 1 < len(stops) else total_distance
        leg_distance = leg_end - mile_marker
        gallons = leg_distance / mpg
        price = float(station['retail_price'])
        cost = gallons * price
        total_cost += cost
        fuel_stops.append(FuelStop(
            station=station, mile_marker=round(mile_marker, 1),
            distance_off_route=round(off_route, 2),
            gallons_purchased=round(gallons, 2), cost=round(cost, 2),
        ))

    return {
        'total_distance_miles': round(total_distance, 1),
        'stops': fuel_stops,
        'total_fuel_cost': round(total_cost, 2),
        'feasible': True,
        'message': None,
    }


def _to_fuel_stop(candidate):
    mile_marker, station, off_route = candidate
    return FuelStop(station=station, mile_marker=round(mile_marker, 1),
                     distance_off_route=round(off_route, 2), gallons_purchased=0, cost=0)
