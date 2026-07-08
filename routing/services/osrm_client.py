"""
Thin wrapper around OSRM's free public routing server.

OSRM (Open Source Routing Machine) exposes a "route" endpoint that, given a
start and end coordinate, returns the driving route: total distance,
duration, and a full polyline geometry. We use the public demo server
(https://router.project-osrm.org), which requires no API key and is fine for
an assessment / low-volume project. For production you'd self-host OSRM or
use a paid tier of a routing provider, but the interface here would stay the
same.

This is the ONE call in the whole request lifecycle that talks to the
routing API.
"""
import requests
from django.conf import settings


class RoutingError(Exception):
    pass


def get_route(start_lat, start_lon, end_lat, end_lon):
    """
    Calls OSRM once and returns:
        {
            'distance_miles': float,
            'duration_seconds': float,
            'geometry': [(lat, lon), (lat, lon), ...]   # full route polyline
        }
    """
    # OSRM wants "lon,lat" ordering, everywhere else in this project we use
    # the more common "lat,lon" ordering - converted right here at the edge.
    coords = f'{start_lon},{start_lat};{end_lon},{end_lat}'
    url = f'{settings.OSRM_BASE_URL}/route/v1/driving/{coords}'

    try:
        resp = requests.get(
            url,
            params={'overview': 'full', 'geometries': 'geojson'},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f'Could not reach routing service: {exc}') from exc

    data = resp.json()
    if data.get('code') != 'Ok' or not data.get('routes'):
        raise RoutingError(f"Routing service returned no route: {data.get('message', data.get('code'))}")

    route = data['routes'][0]
    meters_to_miles = 0.000621371
    geometry = [(lat, lon) for lon, lat in route['geometry']['coordinates']]

    return {
        'distance_miles': route['distance'] * meters_to_miles,
        'duration_seconds': route['duration'],
        'geometry': geometry,
    }
