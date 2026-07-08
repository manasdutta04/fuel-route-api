"""
One-time (well, re-run-when-the-price-list-changes) data load.

What it does:
1. Reads the OPIS fuel price CSV.
2. Geocodes each *unique* city/state pair (not each row -- there are ~8,150
   rows but only ~3,900 distinct cities, so this cuts the geocoding work
   roughly in half) using the free Nominatim (OpenStreetMap) API.
3. Caches geocoding results to a local JSON file so re-running the command
   (e.g. after adding new stations) doesn't re-hit Nominatim for cities we
   already resolved.
4. Bulk-loads everything into the FuelStation table.

This command is intentionally NOT part of the request/response cycle of the
API. Geocoding ~3,900 places at Nominatim's polite rate limit (1 req/sec)
takes about an hour, which is fine to run once as a data-prep step, but
would be a terrible thing to do inside an HTTP request. This is exactly why
the API itself only ever calls the routing service (1 call) plus, if you
give it place names instead of coordinates, a geocoding call for start and
finish (2 more calls) -- 3 calls total, matching the assignment's "one call
ideal, two or three acceptable" requirement.

Usage:
    python manage.py import_fuel_prices data/fuel-prices.csv
    python manage.py import_fuel_prices data/fuel-prices.csv --limit 50   # quick smoke test
"""
import csv
import json
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from routing.models import FuelStation

GEOCODE_CACHE_PATH = Path(settings.BASE_DIR) / 'data' / 'geocode_cache.json'


class Command(BaseCommand):
    help = 'Import the OPIS fuel price CSV and geocode each station city/state.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to the fuel prices CSV file')
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Only process the first N rows (handy for a quick smoke test).',
        )
        parser.add_argument(
            '--sleep', type=float, default=1.0,
            help='Seconds to sleep between Nominatim requests (be polite - default 1s).',
        )

    def handle(self, *args, **options):
        csv_path = Path(options['csv_path'])
        if not csv_path.exists():
            raise CommandError(f'CSV not found at {csv_path}')

        rows = self._read_csv(csv_path, options['limit'])
        self.stdout.write(f'Read {len(rows)} rows from {csv_path}')

        cache = self._load_cache()
        unique_places = sorted({(r['City'].strip(), r['State'].strip()) for r in rows})
        self.stdout.write(f'{len(unique_places)} unique city/state pairs to resolve')

        for i, (city, state) in enumerate(unique_places, start=1):
            key = f'{city}, {state}'
            if key in cache:
                continue
            status_code, coords = self._geocode(city, state)
            if status_code in ('success', 'not_found'):
                cache[key] = coords  # only cache if we got a definitive success or not-found
            if i % 25 == 0:
                self.stdout.write(f'  geocoded {i}/{len(unique_places)}...')
                self._save_cache(cache)  # periodic checkpoint
            time.sleep(options['sleep'])

        self._save_cache(cache)

        stations = []
        skipped = 0
        for row in rows:
            key = f"{row['City'].strip()}, {row['State'].strip()}"
            coords = cache.get(key)
            if not coords:
                skipped += 1
                continue
            stations.append(FuelStation(
                opis_id=int(row['OPIS Truckstop ID']),
                name=row['Truckstop Name'].strip(),
                address=row['Address'].strip(),
                city=row['City'].strip(),
                state=row['State'].strip(),
                rack_id=int(row['Rack ID']) if row.get('Rack ID') else None,
                retail_price=row['Retail Price'],
                latitude=coords['lat'],
                longitude=coords['lon'],
            ))

        FuelStation.objects.all().delete()
        FuelStation.objects.bulk_create(stations, batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f'Loaded {len(stations)} stations ({skipped} skipped - no geocode match).'
        ))

    @staticmethod
    def _read_csv(csv_path, limit):
        with open(csv_path, encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return rows[:limit] if limit else rows

    @staticmethod
    def _load_cache():
        if GEOCODE_CACHE_PATH.exists():
            return json.loads(GEOCODE_CACHE_PATH.read_text())
        return {}

    @staticmethod
    def _save_cache(cache):
        GEOCODE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2))

    def _geocode(self, city, state):
        """
        Resolve a city/state to lat/lon via Nominatim.
        Returns: (status, coords_dict_or_None)
        """
        max_retries = 3
        backoff = 3.0

        for attempt in range(max_retries):
            try:
                resp = requests.get(
                    f'{settings.NOMINATIM_BASE_URL}/search',
                    params={'city': city, 'state': state, 'country': 'USA', 'format': 'json', 'limit': 1},
                    headers={'User-Agent': settings.NOMINATIM_USER_AGENT},
                    timeout=10,
                )
                
                if resp.status_code == 429:
                    self.stdout.write(self.style.WARNING(
                        f'  rate-limited (429) for {city}, {state}. Retrying in {backoff}s... (attempt {attempt+1}/{max_retries})'
                    ))
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                
                resp.raise_for_status()
                results = resp.json()
                if not results:
                    self.stdout.write(self.style.WARNING(f'  no match for {city}, {state}'))
                    return 'not_found', None
                return 'success', {'lat': float(results[0]['lat']), 'lon': float(results[0]['lon'])}
            except (requests.RequestException, ValueError, KeyError) as exc:
                if attempt < max_retries - 1:
                    self.stdout.write(self.style.WARNING(
                        f'  geocode attempt {attempt+1} failed for {city}, {state}: {exc}. Retrying in {backoff}s...'
                    ))
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    self.stdout.write(self.style.WARNING(f'  geocode failed for {city}, {state}: {exc}'))
                    return 'error', None
        return 'error', None
