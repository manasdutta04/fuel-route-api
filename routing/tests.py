"""
Unit tests for the fuel-stop optimizer. These don't hit any network APIs -
they fabricate a route geometry and a handful of stations, which is enough
to verify the core selection/costing logic (this is the part of the
project that's actually "ours" to get right; OSRM and Nominatim are
external services we trust to do their job).
"""
import numpy as np
from django.test import TestCase

from routing.models import FuelStation
from routing.services import fuel_optimizer


class FuelOptimizerTests(TestCase):
    def _build_straight_route(self, start, finish, distance_miles, n=50):
        geometry = [
            (start[0] + (finish[0] - start[0]) * t, start[1] + (finish[1] - start[1]) * t)
            for t in np.linspace(0, 1, n)
        ]
        return {'distance_miles': distance_miles, 'duration_seconds': 0, 'geometry': geometry}, geometry

    def _place_station(self, geometry, t, price, opis_id):
        idx = int(t * (len(geometry) - 1))
        lat, lon = geometry[idx]
        return FuelStation.objects.create(
            opis_id=opis_id, name=f'Stop {opis_id}', address='addr',
            city='City', state='XX', rack_id=1, retail_price=price,
            latitude=lat + 0.01, longitude=lon + 0.01,
        )

    def test_short_trip_needs_no_stops_but_reports_cheapest_station(self):
        route, geometry = self._build_straight_route((41.87, -87.62), (39.95, -86.15), 300)
        self._place_station(geometry, 0.5, 3.00, 1)
        self._place_station(geometry, 0.7, 2.50, 2)

        result = fuel_optimizer.find_fuel_stops(route)

        self.assertTrue(result['feasible'])
        self.assertEqual(len(result['stops']), 1)
        self.assertAlmostEqual(float(result['stops'][0].station['retail_price']), 2.50)

    def test_long_trip_selects_cheapest_reachable_stations(self):
        route, geometry = self._build_straight_route((41.8781, -87.6298), (39.7392, -104.9903), 1003.4)
        prices = [(0.05, 3.10), (0.20, 2.85), (0.35, 3.40), (0.50, 2.75), (0.65, 3.60), (0.80, 2.95), (0.95, 3.20)]
        for i, (t, price) in enumerate(prices):
            self._place_station(geometry, t, price, i + 1)

        result = fuel_optimizer.find_fuel_stops(route)

        self.assertTrue(result['feasible'])
        self.assertEqual(len(result['stops']), 2)
        self.assertAlmostEqual(float(result['stops'][0].station['retail_price']), 2.75)
        self.assertAlmostEqual(float(result['stops'][1].station['retail_price']), 2.95)
        self.assertAlmostEqual(result['total_fuel_cost'], 159.03, places=1)

    def test_infeasible_route_reports_clear_message(self):
        route, geometry = self._build_straight_route((41.8781, -87.6298), (34.0522, -118.2437), 2015)
        # Only place one station, near the very start - a 2000+ mile trip
        # cannot be completed with a single stop and a 500 mile range.
        self._place_station(geometry, 0.02, 3.00, 1)

        result = fuel_optimizer.find_fuel_stops(route)

        self.assertFalse(result['feasible'])
        self.assertIsNone(result['total_fuel_cost'])
        self.assertIn('No priced fuel station found', result['message'])

    def test_no_stations_loaded(self):
        route, _ = self._build_straight_route((41.8781, -87.6298), (39.7392, -104.9903), 1003.4)
        result = fuel_optimizer.find_fuel_stops(route)
        self.assertFalse(result['feasible'])
        self.assertIn('No fuel stations are loaded', result['message'])
