# Fuel Route Optimizer API

A Django REST API that plans fuel stops along a US driving route. Give it a start
and a finish anywhere in the USA and it returns the route (distance, duration,
geometry), a price-optimized sequence of fuel stops for a 500-mile-range vehicle
at 10 MPG, and the trip's fuel consumption and cost — using the provided
`fuel-prices-for-be-assessment.csv` as the only fuel-price source.

```
POST /api/route/
{"start": "Chicago, IL", "finish": "Denver, CO"}
->
{
  "route":    {"distance_miles": 1004.3, "duration_minutes": 1069.2, "geometry": [[41.87556, -87.62435], ...]},
  "fuel_stops": [{"name": "CASEYS #3850", "city": "Wahoo", ..., "distance_from_start_miles": 490.1,
                  "fuel_price": 3.174, "gallons_purchased": 3.07, "fuel_cost": 9.74}, ...],
  "fuel":      {"mpg": 10, "max_range_miles": 500, "total_gallons": 100.43, "total_cost": 146.27, ...}
}
```

---

## 1. Features

- Single endpoint `POST /api/route/` that geocodes two US locations, fetches one
  driving route, and returns a fully costed fuel plan.
- **Exactly one OSRM request per API call** — all station analysis is local.
- Price-aware stop selection: reachable stations only, destination always
  reachable, deterministic minimum-cost plan (Dijkstra), explainable edge costs.
- CSV loaded **once per process** into memory — no per-request file reads, no
  database, no ORM models.
- City+State → approximate coordinates via GeoNames (generated offline; the API
  never calls GeoNames at runtime, and never invents coordinates).
- Structured JSON errors with appropriate status codes (400/422/500/502), no
  stack traces ever leak to clients.
- 30 tests, all external APIs mocked — fully deterministic, offline.

## 2. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.13 |
| Web framework | Django 6.1.1 (latest stable) |
| API toolkit | Django REST Framework 3.18.1 |
| Routing | OSRM public demo server (`router.project-osrm.org`) — 1 call/request |
| Geocoding | Photon (`photon.komoot.io`) — free, keyless, returns country codes |
| City coordinates | GeoNames `US.txt` dump (offline, one-time build script) |
| Data processing | pandas + numpy (one-time load, vectorized local filtering) |
| Storage | none — CSV + generated coordinate CSV held in memory |

## 3. Project structure

```
Fuel_route/
├── manage.py
├── requirements.txt
├── .env.example                  # documented environment variables (no secrets)
├── .gitignore
├── README.md
├── fuel-prices-for-be-assessment.csv   # provided dataset (only fuel-price source)
├── data/
│   └── city_coordinates.csv      # generated: 3,792 City+State -> lat/lon (GeoNames)
├── scripts/
│   └── build_city_coordinates.py # one-time GeoNames download + match + report
├── config/
│   ├── settings.py               # Django settings + optimizer configuration
│   ├── urls.py                   # -> api/ include
│   └── wsgi.py
└── route_api/
    ├── apps.py
    ├── serializers.py            # request validation (start/finish)
    ├── views.py                  # POST /api/route/ orchestration + error mapping
    ├── urls.py
    ├── exceptions.py             # ApiError hierarchy -> HTTP status codes
    ├── services/
    │   ├── geo_utils.py          # normalization, haversine, polyline, projection
    │   ├── geocoding.py          # Photon client (US validation, in-process cache)
    │   ├── routing.py            # OSRM client (1 call: distance+duration+geometry)
    │   ├── fuel_data.py          # CSV load-once, cleaning, US filter, stats, join
    │   └── optimizer.py          # corridor filtering + Dijkstra fuel planner
    └── tests/                    # 30 tests + fixture CSVs + offline HTTP mocks
```

## 4. Requirements & installation

```bash
python -m venv .venv
# Windows:
.venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS/Linux:
.venv/bin/pip install -r requirements.txt
```

Dependencies: `Django==6.1.1`, `djangorestframework==3.18.1`, `requests`,
`pandas`, `numpy` (pinned in `requirements.txt`).

## 5. Configuration

Everything has a working default; `.env.example` documents the optional
environment variables read by `config/settings.py`:

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev-only value | set your own in production |
| `DJANGO_DEBUG` | `0` | `1` enables debug mode |
| `FUEL_CSV_PATH` | `fuel-prices-for-be-assessment.csv` | fuel-price dataset |
| `CITY_COORDS_PATH` | `data/city_coordinates.csv` | GeoNames city coordinates |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | routing service |
| `PHOTON_BASE_URL` | `https://photon.komoot.io` | geocoding service |
| `ROUTE_CORRIDOR_MILES` | `25` | on-route corridor tolerance |

No API keys are required for either external service, and none are committed.

## 6. Running the server

```bash
.venv\Scripts\python.exe manage.py runserver        # Windows
.venv/bin/python manage.py runserver                # macOS/Linux
```

The CSV is loaded on the first request (~1 s) and reused for every request
afterwards (subsequent requests do no file I/O — at most 3 HTTP calls: two
Photon geocodes, cached across requests, plus one OSRM route).

## 7. Regenerating city coordinates (optional)

`data/city_coordinates.csv` is committed, so this is only needed if the fuel CSV
changes:

```bash
python scripts/build_city_coordinates.py
```

It downloads the GeoNames `US.txt` dump (cached), keeps the most populous place
per normalized City+State, and **reports match statistics**:

```
US unique city+state pairs in CSV : 3808
Matched to GeoNames               : 3792
Unmatched (excluded at runtime)   : 16
```

Unmatched pairs (e.g. `bronx, NY`, `jacumba, CA`) are excluded at runtime —
coordinates are never guessed or invented.

## 8. API reference

### `POST /api/route/`

**Request body (JSON):**

| Field | Type | Required | Description |
|---|---|---|---|
| `start` | string | yes | US start location, e.g. `"Chicago, IL"` |
| `finish` | string | yes | US finish location, e.g. `"Denver, CO"` |

**Success — `200 OK`** (abbreviated: the real response has 9,259 geometry
points and 3 fuel stops; values below are from an actual request):

```json
{
  "start": "Chicago, IL",
  "finish": "Denver, CO",
  "route": {
    "distance_miles": 1004.3,
    "duration_minutes": 1069.2,
    "geometry": [[41.87556, -87.62435], "..."]
  },
  "fuel_stops": [
    {
      "name": "CASEYS #3850",
      "address": "US HWY 77",
      "city": "Wahoo",
      "state": "NE",
      "latitude": 41.21139,
      "longitude": -96.6203,
      "distance_from_start_miles": 490.1,
      "fuel_price": 3.174,
      "gallons_purchased": 3.07,
      "fuel_cost": 9.74
    },
    "... 2 more stops: FAT DOGS LINCOLN TC (Lincoln, NE), AKAL TRAVEL CENTER (Waco, NE) ..."
  ],
  "fuel": {
    "mpg": 10,
    "max_range_miles": 500,
    "total_gallons": 100.43,
    "total_cost": 146.27,
    "gallons_purchased": 51.43
  }
}
```

Notes:

- `route.geometry` is a list of `[latitude, longitude]` pairs decoded from the
  OSRM polyline (rounded to 5 decimals) — order is **lat, lon**.
- `fuel_stops` is ordered by distance from start; an empty array means the
  whole route fits in the initial tank (cost 0).
- `distance_from_start_miles` is the station's mile marker **along the route**.
- `fuel.total_gallons` = `distance_miles / 10` (total consumption);
  `gallons_purchased` = gallons actually bought at stops (less than
  `total_gallons` because the trip starts on a full tank).
- `fuel.total_cost` = sum of the per-stop `fuel_cost` values.

### Errors — all JSON, no stack traces

| Status | Condition | Example body |
|---|---|---|
| `400` | missing/blank/invalid `start` or `finish` | `{"start": ["This field is required."]}` |
| `400` | malformed JSON | `{"detail": "JSON parse error - ..."}` |
| `400` | location cannot be resolved | `{"error": "Could not resolve location 'Nowhereville, ZZ'."}` |
| `400` | location outside the USA | `{"error": "Location 'Toronto, ON, Canada' resolves to 'Canada', outside the USA."}` |
| `400` | start == finish | `{"error": "Start and finish must be different locations."}` |
| `422` | no feasible fuel plan (a gap between reachable stations exceeds 500 miles, or the destination can't be reached) | `{"error": "No feasible fuel-stop plan exists within the 500-mile vehicle range ..."}` |
| `500` | fuel CSV missing/unreadable | `{"error": "The fuel-price CSV file was not found at '...'."}` |
| `502` | Photon geocoding unavailable | `{"error": "Geocoding service is unavailable."}` |
| `502` | OSRM routing unavailable/no route | `{"error": "Routing service is unavailable."}` |

## 9. Postman-ready examples

`POST http://127.0.0.1:8000/api/route/` with header `Content-Type: application/json`:

```json
{"start": "New York, NY", "finish": "Boston, MA"}
```
→ 200, 213.5 miles, no fuel stops, `total_gallons: 21.35`, `total_cost: 0`.

```json
{"start": "Chicago, IL", "finish": "Denver, CO"}
```
→ 200, 1004.3 miles, 3 stops (Wahoo NE, Lincoln NE, Waco NE), `total_cost: 146.27`.

```json
{"start": "Miami, FL", "finish": "Seattle, WA"}
```
→ 200, 3,302.7 miles, **15 stops** across 7 states, `total_cost: 863.60`
(every leg ≤ 500 miles; cross-country multi-stop plan).

```json
{"start": "Toronto, ON, Canada", "finish": "Chicago, IL"}
```
→ 400, `"Location 'Toronto, ON, Canada' resolves to 'Canada', outside the USA."`

```json
{"finish": "Denver, CO"}
```
→ 400, `{"start": ["This field is required."]}`

cURL equivalent:

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "finish": "Denver, CO"}'
```

## 10. Data pipeline (fuel CSV)

The provided CSV has **no latitude/longitude columns**. Loading
(`route_api/services/fuel_data.py`) happens once per process:

1. Read CSV (utf-8-sig), validate the 7 required columns.
2. Clean: strip whitespace on City/State/Name/Address, coerce `Retail Price` to
   numeric, drop rows missing name/city/state/price or with price ≤ 0.
3. Filter to **US states only** — Canadian/non-US rows (`AB`, `BC`, `ON`, …)
   are excluded (the dataset contains 620 such rows).
4. Build normalized `(city, state)` keys and join to `data/city_coordinates.csv`
   (GeoNames). Rows whose city has no coordinates are **excluded**, never
   approximated to a wrong place.
5. Deduplicate repeated listings of the same station (same name+city+state),
   keeping the cheapest price.
6. Log the loading statistics (below) and cache the result for the process.

**Measured statistics of the provided dataset** (logged on first load):

```
{'total_rows': 8151, 'invalid_rows': 0, 'us_rows': 7531, 'non_us_rows': 620,
 'unique_city_state_pairs': 3808, 'matched_pairs': 3792, 'unmatched_pairs': 16,
 'excluded_unmatched_rows': 22, 'station_count': 6816}
```

- 7,531 US rows of 8,151 total (620 non-US excluded).
- 3,808 unique US City+State pairs; **3,792 matched to GeoNames (99.6%)**;
  16 unmatched pairs → 22 rows excluded (`bronx, NY`, `jacumba, CA`, …).
- 7,509 rows carry coordinates; duplicate listings collapse to **6,816
  stations** in memory.
- Retail prices in the file range $2.687 – $6.399.

## 11. Geocoding (Photon)

`route_api/services/geocoding.py` calls `https://photon.komoot.io/api/?q=...`
(free, no key, no registration) with `limit=1`, and validates
`properties.countrycode == "US"` — anything else is rejected with a 400 naming
the country. Empty results → 400 "Could not resolve location". Results are
cached in-process, so repeated requests for the same location make no external
calls. Service failures (timeouts, 5xx) → 502.

## 12. Routing (OSRM) — one call per request

`route_api/services/routing.py` issues **exactly one** request:

```
GET {OSRM_BASE_URL}/route/v1/driving/{lon},{lat};{lon},{lat}
    ?overview=full&geometries=polyline&alternatives=false
```

and extracts `distance` (m→miles), `duration` (s→minutes) and the full
`polyline` geometry (decoded locally). **No per-station routing requests are
ever made** — station filtering/projection happens entirely against this one
geometry. Failures/no-route → 502. Verified in server logs: one `OSRM route:`
line per API call, and the test suite asserts `osrm_calls == 1`.

## 13. Station filtering (corridor)

1. Decode the polyline and compute cumulative mile markers, **scaled** so the
   marker at the route end equals OSRM's reported distance (great-circle sums
   can differ ~1–3% from road distance).
2. Bounding-box pre-filter of the in-memory station arrays (numpy, padded by
   the corridor).
3. Vectorized nearest-vertex lookup per candidate, then projection onto the
   adjacent segments to get `(along_route_miles, perpendicular_miles)`.
4. Keep stations whose perpendicular distance ≤ `ROUTE_CORRIDOR_MILES`
   (default **25 miles** — chosen because coordinates are *city centroids*,
   not exact station positions; a typical city spans ~10–15 miles). Stations
   outside the corridor are silently excluded — never routed to, never priced.

**Station coordinates are approximate city-level locations** (GeoNames place
centroids) and are explicitly not claimed to be exact pump locations.

## 14. Fuel optimization

**Vehicle model:** 10 MPG, 500-mile max range (50-gallon tank), full tank at
departure — **that initial fuel is not charged to the trip**.

**Cost model:** fuel is accounted *per leg*: each stop buys exactly the gallons
its outbound leg will burn (`leg_miles / 10`), charged at the station where
that leg begins; the starting tank is free, and fuel left in the tank from an
earlier leg is not netted against a purchase (tank level between stops is not
modeled — a purchase never exceeds what the next leg requires):

```
cost(edge i → j) = (mile_j − mile_i) / 10 × price(station_i)   [start price = 0]
total_cost = Σ cost over the chosen path
```

**Algorithm** (`route_api/services/optimizer.py`):

1. Nodes = route start (mile 0) + every corridor candidate (sorted by mile
   marker) + destination (mile D).
2. Edge `i → j` exists iff `0 < mile_j − mile_i ≤ 500` (edges only point
   forward along the route, so a single left-to-right relaxation pass is a
   full Dijkstra — O(n²) worst case over a few hundred candidates).
3. Shortest path over edge costs = minimum fuel cost. Ties break toward
   **fewer stops** (deterministic).
4. If the destination is unreachable (some gap between reachable stations
   exceeds 500 miles, or no station can even be reached), respond **422** —
   a globally-cheapest station sitting behind a >500-mile gap can never
   appear in a plan because no feasible path reaches it.

**Why not greedy?** Greedy "take the next cheapest station" can strand the
vehicle at its destination (a cheap station 550 miles from the next station
makes the last leg impossible). Dijkstra over the reachability graph makes
stranding structurally impossible: only ≤500-mile legs are ever chosen, so the
destination always stays reachable if a plan exists.

**Worked example** (live, Chicago → Denver, 1004.3 mi). The free starting tank
covers the first 490.1-mile leg (Chicago → Wahoo); each stop then buys fuel
for its **outbound** leg:

| Stop | Mile | Price | Outbound leg funded | Gallons bought | Cost |
|---|---|---|---|---|---|
| CASEYS #3850, Wahoo NE | 490.1 | 3.174 | → Lincoln, 30.7 mi | 3.07 | 9.74 |
| FAT DOGS LINCOLN TC | 520.8 | 3.099 | → Waco, 39.0 mi | 3.90 | 12.09 |
| AKAL TRAVEL CENTER, Waco NE | 559.8 | 2.799 | → Denver, 444.5 mi | 44.46 | 124.44 |

Total cost **$146.27** = 9.74 + 12.09 + 124.44; purchased **51.43 gal**
(= 3.07 + 3.90 + 44.46); consumed **100.43 gal** (= 1004.3 / 10). Gallons are
computed from unrounded mile markers and rounded for display, so an individual
row may differ by ≤ 0.01 from displayed-marker arithmetic (e.g. 44.46 vs
444.5 / 10). The plan detours through two mid-price stops precisely so the
cheap Waco station carries the 444-mile final leg — exactly the behavior the
optimizer should exhibit.

## 15. Error handling

All failures flow through `route_api/exceptions.py` (`ApiError` hierarchy with
status codes) and are caught in `views.py`, which returns
`{"error": "<message>"}` with the mapped status. DRF handles request
validation (400 with field-level messages) and malformed JSON (400
`{"detail": ...}`). Unexpected exceptions are logged server-side with
traceback (`logger.exception`) and answered with a generic
`{"error": "Internal server error."}` — **clients never see a stack trace**
(debug mode off by default).

Coverage: missing fields, invalid field types, unresolvable location,
non-USA location, same start/finish, Photon outage, OSRM outage, infeasible
fuel plan, missing/unreadable CSV — each has a test.

## 16. Testing

```bash
.venv\Scripts\python.exe manage.py check     # System check: no issues
.venv\Scripts\python.exe manage.py test      # 30 tests, all offline
```

**Result: 30/30 passing.** All HTTP (Photon + OSRM) is mocked via a URL-based
dispatcher; fixture CSVs model an exact synthetic route so optimizer output is
hand-verifiable (e.g. the expected optimal plan on the 1,000-mile fixture line
is stops at mile 500 + 600 for exactly $163.00).

Required scenarios → tests (`route_api/tests/test_route_api.py`,
`test_fuel_data.py`, `test_geo_utils.py`):

| # | Scenario | Test |
|---|---|---|
| 1 | valid request | `test_valid_request_returns_full_plan` |
| 2 | missing start | `test_missing_start_returns_400` |
| 3 | missing finish | `test_missing_finish_returns_400` |
| 4 | invalid location | `test_invalid_location_returns_400` |
| 5 | non-USA location | `test_non_usa_location_returns_400` |
| 6 | short route → no stop | `test_short_route_returns_no_fuel_stops` |
| 7 | long route → multiple stops | `test_long_route_returns_multiple_stops` |
| 8 | outside-corridor ignored | `test_station_outside_corridor_is_ignored` |
| 9 | >500-mi gap not used | `test_stations_beyond_vehicle_range_not_selected` |
| 10 | cheap-unreachable not selected | `test_globally_cheapest_unreachable_station_not_selected` |
| 11 | destination reachability | `test_destination_reachability_is_enforced` |
| 12 | fuel consumption calc | `test_total_gallons_matches_route_distance` |
| 13 | fuel cost calc | `test_total_cost_matches_sum_of_stop_costs` |
| 14 | routing failure | `test_routing_failure_returns_502` |
| 15 | CSV loading behavior | `test_csv_loading_reports_stats` (+7 more loading tests) |
| — | geocoding failure 502 | `test_geocoding_service_failure_returns_502` |
| — | same start/finish 400 | `test_same_start_and_finish_returns_400` |
| — | CSV missing → 500 | `test_missing_csv_returns_500_json_error` |
| — | invalid field types 400 | `test_invalid_field_types_return_400` |
| — | polyline decode round-trip | `test_polyline_roundtrip*` |
| — | city/state normalization | `test_normalize_city_equivalences` |

The tests additionally assert: one OSRM call per request, every leg ≤ 500
miles, stop-level costs summing to `total_cost`, `gallons_purchased` matching
leg math, and load-once singleton semantics.

## 17. Performance notes

- CSV + coordinates load **once** (~1 s, pandas) and stay in memory;
  requests after the first perform no file I/O.
- One OSRM call + two Photon calls (cached across requests) per route request —
  never per-station calls.
- Station filtering is vectorized (numpy bbox + chunked distance matrix);
  the optimizer is a single forward pass over ≤ a few thousand candidates.
- Full-test-suite runtime: < 1 s (everything mocked).

## 18. Design decisions & assumptions

1. **City-level coordinates** — the CSV has no coordinates, so each station
   uses its city's GeoNames centroid (most populous place with that name).
   Corridor = 25 miles; unmatched cities are excluded, never guessed.
2. **US only** — Canadian rows in the CSV (provinces AB/BC/ON/…) are dropped at
   load; geocoded locations must return `countrycode == "US"`.
3. **Charging model** — the truck leaves with a full tank (not charged); each
   stop buys exactly the fuel for the next leg. This makes every stop's cost
   explainable ("this stop paid for these miles") and guarantees total cost =
   Σ stop costs.
4. **Determinism** — candidates sorted by (mile, price, name); equal-cost paths
   prefer fewer stops; responses are stable across runs.
5. **Feasibility over completeness** — if no chain of ≤500-mile legs reaches
   the destination, the API refuses (422) rather than returning an impossible
   plan.
6. **No database/models/admin/auth/frontend/Celery/Redis/Docker** — out of
   scope for this assessment; state is the two in-memory CSV-derived datasets.
7. **Public demo endpoints** for OSRM/Photon are used (no keys exist for
   them); base URLs are configurable for self-hosted deployments.
8. **Geometry order** is `[latitude, longitude]` (documented above).

## 19. Manual verification record

Run against the live server (`runserver 127.0.0.1:8017`) with real Photon/OSRM:

| Request | Result |
|---|---|
| `{"start": "New York, NY", "finish": "Boston, MA"}` | 200 — 213.5 mi, 280.7 min, 7,464 geometry points, **0 stops**, 21.35 gal, $0 |
| `{"start": "Chicago, IL", "finish": "Denver, CO"}` | 200 — 1004.3 mi, 1069.2 min, **3 stops** (Wahoo/Lincoln/Waco, NE), legs 490.1/30.7/39.0/444.5 mi ≤ 500, $146.27 = 9.74+12.09+124.44 |
| `{"start": "Toronto, ON, Canada", ...}` | 400 — `"resolves to 'Canada', outside the USA."` |
| `{"finish": "Denver, CO"}` | 400 — `{"start": ["This field is required."]}` |
| `{"start": "Nowhereville, ZZ", ...}` | 400 — `"Could not resolve location 'Nowhereville, ZZ'."` |
| `{"start": "Denver, CO", "finish": "Denver, CO"}` | 400 — `"Start and finish must be different locations."` |
| `{"start": "Miami, FL", "finish": "Seattle, WA"}` | 200 — 3,302.7 mi, **15 stops** (GA/ID/MO/MT/NE/SD/TN), 35,173 geometry points, all 16 legs ≤ 500 mi (max 486.9), $863.60 = Σ stop costs |

Server log confirms: CSV loaded once (statistics in §10), **2 OSRM calls for 2
successful requests**, real Photon geocodes (`New York, NY → 40.71273,
-74.00602`, `Chicago, IL → 41.87556, -87.62442`).

**Re-verified 2026-10-01** after a full audit review: `manage.py check` clean,
`makemigrations --check` reports no changes, **30/30 tests pass**, and every
example above was reproduced against a live server running the reviewed code
(Chicago→Denver and NY→Boston reproduce exactly; Miami→Seattle and Toronto as
documented). Note: OSRM's returned geometry starts ~5 m from the requested
point (road snap), so `route.geometry[0]` is `41.87556, -87.62435` while the
Photon geocode is `41.87556, -87.62442` — geometry is returned exactly as
OSRM provides it.
