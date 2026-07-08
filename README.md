# Fuel Route API

A Django REST API that, given a start and finish location in the USA, returns:

- the driving route (as geometry you can draw on a map),
- an optimal (cheapest) sequence of fuel stops so the vehicle never exceeds
  its 500-mile range, and
- the total estimated fuel cost for the trip, assuming 10 miles/gallon.

## Why it's built this way

The two hard constraints in the brief were: **be fast**, and **call the
map/routing API as few times as possible (1 ideal, 2-3 acceptable)**. Those
two constraints point at the same design decision: do as much work as
possible *before* a request ever comes in, so a request itself is cheap.

- **Fuel station coordinates are geocoded once, offline**, via a management
  command (`import_fuel_prices`), not on every request. The ~8,150 rows in
  the price list collapse to ~3,900 unique city/state pairs, which are
  geocoded against Nominatim (free, OpenStreetMap) and cached to
  `data/geocode_cache.json` plus the database, so this only ever needs to
  run once (or again if the price list changes).
- **Routing costs exactly one external call per request**: a single call to
  OSRM's free public routing server (`router.project-osrm.org`), which
  returns the full route geometry and distance in one round trip.
- **Geocoding start/finish, if you pass place names, costs up to two more
  calls** (one each), which puts a typical request at 3 calls total - right
  at the "acceptable" end of the brief. If you pass coordinates directly
  (`start_lat`/`start_lon`/`finish_lat`/`finish_lon`) instead of place
  names, geocoding is skipped entirely and the request makes exactly **one**
  external call, matching "one call is ideal."
- **Matching fuel stations to the route, and picking which ones to stop at,
  is done entirely in local memory** using a KD-tree (`scipy.spatial.cKDTree`)
  over resampled route points, plus a greedy "cheapest reachable station"
  selection. No network calls, no per-station API lookups - this is what
  keeps the endpoint fast regardless of how many stations are in the
  corridor.

## The fuel-stop algorithm

1. Resample the route polyline into evenly-spaced points (every 5 miles).
2. Build a KD-tree over those points and, for every station in the
   database, find its nearest route point. If a station is within 3 miles
   of the route, it's a candidate, tagged with its approximate mile marker.
3. If the whole trip fits in one 500-mile tank, no stop is required (the
   cheapest nearby station is still reported for reference).
4. Otherwise, greedily walk the route: from the current position, look at
   every candidate station within the next 500 miles, and refuel at the
   cheapest one (ties broken by whichever is furthest along, to keep the
   most slack for the next leg). Repeat until the remaining distance to the
   destination fits in one tank.
5. Cost is charged per leg: the gallons used to cover the distance from one
   stop to the next (or to the destination, for the last stop) at that
   stop's price. The very first leg, from the start to the first stop, is
   assumed to be covered by fuel the vehicle already had before the trip
   began, so it isn't charged.

This greedy approach is the standard, provably-optimal strategy for
"minimize cost given a range constraint and refuel-to-full stops" (a
variant of the classic gas station / interval scheduling problem).

## Project layout

```
fuel_route/            Django project settings/urls
routing/
  models.py            FuelStation model (price list + geocoded lat/lon)
  serializers.py        Request validation
  views.py              RouteView - the one API endpoint
  services/
    geocoding.py        Nominatim wrapper (request-time, cached)
    osrm_client.py       OSRM wrapper (the one routing call)
    fuel_optimizer.py    KD-tree matching + greedy stop selection
  management/commands/
    import_fuel_prices.py   Offline geocode + load script
  tests.py              Unit tests for the optimizer (no network needed)
data/
  fuel-prices.csv       The provided price list
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate

# One-time: geocode the price list and load it into the DB.
# This takes ~an hour (Nominatim's usage policy asks for max 1 req/sec,
# and there are ~3,900 unique cities to resolve) - grab a coffee.
# Progress is checkpointed to data/geocode_cache.json, so it's safe to
# Ctrl+C and re-run; already-resolved cities won't be re-queried.
python manage.py import_fuel_prices data/fuel-prices.csv

python manage.py runserver
```

Quick smoke test without waiting an hour: run the import with `--limit 50`
to only geocode/load the first 50 rows.

## API

### `POST /api/route/`

**By place name** (costs up to 3 external calls: 2 geocode + 1 route):

```bash
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "finish": "Denver, CO"}'
```

**By coordinates** (costs exactly 1 external call: 1 route):

```bash
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start_lat": 41.8781, "start_lon": -87.6298, "finish_lat": 39.7392, "finish_lon": -104.9903}'
```

**Response:**

```json
{
  "start": {"lat": 41.8781, "lon": -87.6298},
  "finish": {"lat": 39.7392, "lon": -104.9903},
  "total_distance_miles": 1003.4,
  "route_geometry": [[41.8781, -87.6298], "..."],
  "fuel_stops": [
    {
      "name": "TA COUNCIL BLUFFS TRAVEL CENTER",
      "address": "I-29 & I-80, EXIT 3",
      "city": "Council Bluffs",
      "state": "IA",
      "price_per_gallon": 3.7256,
      "mile_marker": 445.0,
      "distance_off_route_miles": 0.4,
      "gallons_purchased": 28.5,
      "cost": 106.18
    }
  ],
  "total_fuel_cost": 214.87,
  "feasible": true,
  "message": null
}
```

If a route can't be completed with the loaded station data (a gap longer
than 500 miles between usable stations), `feasible` is `false` and
`message` explains why.

## Tests

```bash
python manage.py test routing
```

These test the optimizer logic directly against synthetic routes/stations,
so they run instantly and don't depend on OSRM or Nominatim being up.

## Notes / things I'd do differently for production

- OSRM's public demo server is rate-limited and not meant for production
  traffic - I'd self-host OSRM (it's open source) or use a paid routing
  provider behind the same `osrm_client` interface.
- Nominatim similarly asks for very light, non-commercial use - a
  production system would use a paid geocoder or a licensed dataset.
- The 3-mile "how far off the route counts as usable" radius and 5-mile
  sampling interval are both configurable in `settings.py` and were chosen
  to balance recall (finding real stations) against the KD-tree query cost.
