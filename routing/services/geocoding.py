"""
Geocodes free-text start/finish locations (e.g. "Chicago, IL" or
"350 5th Ave, New York, NY") into lat/lon using Nominatim, with a Django
cache in front of it so the same location typed twice in a row doesn't
cost a second network call.

If the caller already provides lat/lon (recommended for the fastest, most
call-frugal use of this API - see README), this module is never touched at
all and the request only makes ONE outbound call, to the routing service.
"""
import json
from pathlib import Path
import requests
from django.conf import settings
from django.core.cache import cache
from routing.models import FuelStation

GEOCODE_CACHE_PATH = Path(settings.BASE_DIR) / 'data' / 'geocode_cache.json'


class GeocodingError(Exception):
    pass


def geocode(place_text):
    """Returns (lat, lon) for a free-text place string."""
    text = place_text.strip().lower()
    cache_key = f'geocode:{text}'
    
    # 1. Check Django memory cache
    cached = cache.get(cache_key)
    if cached:
        return cached

    # 2. Check geocode_cache.json on disk (contains 3,400+ cities)
    try:
        if GEOCODE_CACHE_PATH.exists():
            file_cache = json.loads(GEOCODE_CACHE_PATH.read_text(encoding='utf-8'))
            for key, val in file_cache.items():
                if key.strip().lower() == text and val is not None:
                    res = (val['lat'], val['lon'])
                    cache.set(cache_key, res, timeout=60 * 60 * 24)
                    return res
    except Exception:
        pass

    # 3. Check FuelStation database for a station in this city/state
    parts = [p.strip() for p in text.split(',')]
    if len(parts) == 2:
        city_name, state_code = parts[0], parts[1]
        station = FuelStation.objects.filter(
            city__iexact=city_name,
            state__iexact=state_code,
            latitude__isnull=False,
            longitude__isnull=False
        ).first()
        if station:
            res = (station.latitude, station.longitude)
            cache.set(cache_key, res, timeout=60 * 60 * 24)
            return res

    # 4. Fallback to Nominatim API if not found locally
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
