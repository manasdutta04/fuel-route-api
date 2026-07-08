"""
Geocodes free-text start/finish locations (e.g. "Chicago, IL" or
"350 5th Ave, New York, NY") into lat/lon using Nominatim, with a Django
cache in front of it so the same location typed twice in a row doesn't
cost a second network call.

If the caller already provides lat/lon (recommended for the fastest, most
call-frugal use of this API - see README), this module is never touched at
all and the request only makes ONE outbound call, to the routing service.
"""
import requests
from django.conf import settings
from django.core.cache import cache


class GeocodingError(Exception):
    pass


def geocode(place_text):
    """Returns (lat, lon) for a free-text place string."""
    cache_key = f'geocode:{place_text.strip().lower()}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        resp = requests.get(
            f'{settings.NOMINATIM_BASE_URL}/search',
            params={'q': place_text, 'format': 'json', 'limit': 1, 'countrycodes': 'us'},
            headers={'User-Agent': settings.NOMINATIM_USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise GeocodingError(f'Could not reach geocoding service: {exc}') from exc

    results = resp.json()
    if not results:
        raise GeocodingError(f'Could not resolve location: "{place_text}"')

    result = (float(results[0]['lat']), float(results[0]['lon']))
    cache.set(cache_key, result, timeout=60 * 60 * 24)  # cache for a day
    return result
