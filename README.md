# FuelRoute API & Interactive Map Dashboard

A Django web application and REST API that calculates driving routes within the USA, optimizes fuel stops based on fuel price lists (assuming a 500-mile vehicle range), and computes total trip costs at 10 MPG. 

This application features a dark-mode glassmorphic dashboard with an interactive Leaflet.js map displaying routes and fuel stops in real-time.

---

## Deployed Link (Render)

You can access the live, interactive demo of the application here:
* **[Live Demo Link](https://fuel-route-api-3aw2.onrender.com/)**

---

## Key Features

* **Interactive Map Dashboard**: A single-page dashboard at the root path (`/`) that plots routes, start/destination markers, and recommended fuel stop pins. Users can click on pins or sidebar itinerary items to smoothly pan to and inspect stations.
* **Smart Fuel Stop Optimizer**: Implements a greedy algorithm for the gas station problem. It resamples the OSRM route geometry and uses a SciPy KD-tree (`cKDTree`) to locate candidate fuel stops within 3 miles of the corridor.
* **Call-Frugal Design**: 
  - Coordinates text searches in up to 3 calls total (2 Nominatim geocoding + 1 OSRM routing).
  - Skips geocoding entirely when coordinate parameters (`start_lat`/`start_lon`/`finish_lat`/`finish_lon`) are provided, bringing network calls down to exactly one call.
* **Robust Offline Geocoding & Importer**: Loads fuel stops from OPIS CSV data, caching results to `data/geocode_cache.json` to prevent repeat hits. Includes exponential backoff retry logic for 429 Too Many Requests limits.


---

## Installation & Local Setup

### 1. Clone the Repository
```bash
git clone https://github.com/manasdutta04/fuel-route-api.git
cd fuel-route-api
```

### 2. Set Up the Virtual Environment
**On Windows PowerShell:**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```
**On macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Requirements
```bash
pip install -r requirements.txt
```

### 4. Run Migrations
```bash
python manage.py migrate
```

### 5. Import and Geocode Fuel Prices
To geocode the unique cities, we use Nominatim. The script is rate-limit aware and retries on 429 errors. To load the stations using the cached coordinates:
```bash
python manage.py import_fuel_prices data/fuel-prices.csv
```
*(Since `data/geocode_cache.json` is committed in the repository, this run uses the pre-geocoded cache and completes in just a few seconds.)*

### 6. Run the Server
```bash
python manage.py runserver
```
Visit `http://127.0.0.1:8000/` in your browser to view the interactive dashboard.

---

## API Documentation

### `POST /api/route/`

#### 1. Search by Place Names (Text Search)
* **Headers**: `Content-Type: application/json`
* **Request**:
  ```json
  {
    "start": "Chicago, IL",
    "finish": "Denver, CO"
  }
  ```
* **Response**:
  ```json
  {
    "start": { "lat": 41.8781, "lon": -87.6298 },
    "finish": { "lat": 39.7392, "lon": -104.9903 },
    "total_distance_miles": 1003.4,
    "route_geometry": [[41.8781, -87.6298], "..."],
    "fuel_stops": [
      {
        "name": "TA COUNCIL BLUFFS TRAVEL CENTER",
        "address": "I-29 & I-80, EXIT 3",
        "city": "Council Bluffs",
        "state": "IA",
        "price_per_gallon": 3.7256,
        "latitude": 41.2238,
        "longitude": -95.8427,
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

#### 2. Search by Coordinates (Fastest - 1 Route Call)
* **Headers**: `Content-Type: application/json`
* **Request**:
  ```json
  {
    "start_lat": 41.8781,
    "start_lon": -87.6298,
    "finish_lat": 39.7392,
    "finish_lon": -104.9903
  }
  ```

---

## Testing

Run the unit test suite locally to verify the optimizer logic (runs mock route geometries instantly without hitting external services):
```bash
python manage.py test
```

---

## How it works: The Optimization Algorithm

1. **Resampling**: The route polyline is resampled into evenly spaced markers (every 5 miles) to ensure uniform density along straight highway legs.
2. **KD-Tree Corridor Filter**: We build a KD-tree using `scipy.spatial.cKDTree` on the route coordinates. Fuel stations are projected and queried against the KD-tree. Any station within 3 miles off-route is filtered into a candidate list.
3. **Greedy Fuel Stops Allocation**: 
   - If the trip is less than or equal to 500 miles, no refueling stops are required.
   - Otherwise, we look ahead up to 500 miles from our current position and pick the cheapest station. If there is a tie, we pick the station furthest along to maximize range slack.
   - The cost of fuel for the leg is computed based on distance covered and that stop's price per gallon at 10 MPG.
