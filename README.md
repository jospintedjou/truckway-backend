# Truckway: weather-aware truck routing

Truckway suggests the safest of three truck routes for a given load. It reads the weather forecast at the exact time the truck reaches each checkpoint, then classifies the risk using the assessment's rules, including the load rules.

```
frontend/   React + TypeScript (Vite), MapLibre + deck.gl, nginx in production
backend/    Django + DRF, risk engine, TomTom + Open-Meteo adapters, Redis cache
```

## Screens

1. **Loader.** Shows the brand and wakes the API in the background.
2. **Trip details.** Origin and destination (with suggestions), departure time (local to the origin), and load weight. The load slider marks the 30,000 and 40,000 lb thresholds and previews how wind will be classified. You also pick how often to sample (10, 25 or 50 miles).
3. **Live map.** The routes, with checkpoints colored by risk and a risk heatmap of the corridor for your load.
   - **Trip pill (top):** the edit icon reopens the trip details in a dialog and re-plans in place.
   - **Time bar (bottom):** covers 0–48 h after departure. Dragging or playing it moves the heatmap to that hour and moves each truck to where it would be. Its colored track is the risk the selected truck meets hour by hour. The same bar holds the risk-map switch, the five-level legend, and a one-line summary of the corridor forecast.
   - **Bottom sheet (side panel on desktop):** a compact verdict that expands to show its reasons, the route cards with risk ribbons, and the checkpoint timeline.

### Maps

- **WebGL map (default):** MapLibre with the CARTO dark basemap, retuned so state lines, highways and city names stand out. deck.gl draws the routes, checkpoints and risk cells.
- **Simple map:** used when the browser can't create a WebGL context, for example with hardware acceleration off, in some virtual machines, or over Remote Desktop. It's Leaflet on a 2D canvas over darkened OpenStreetMap tiles, with the same data and interactions. It's downloaded only when needed, so the normal path pays nothing for it. The app switches to it automatically, including if the WebGL map crashes later. Force it with `/trip?map=lite`.

## How it works

1. **Routes.** TomTom Routing in truck mode, with `vehicleWeight` = load + tare (`TRUCK_TARE_LB`), `departAt` and up to 2 alternatives. If TomTom returns fewer than 3 routes, extra ones are forced through a via-point on each side of the straight line, and near-duplicates are dropped.
2. **Checkpoints** every 10, 25 or 50 miles along the route, plus the destination.
3. **ETA at each checkpoint.** It comes from TomTom's guidance: each instruction gives the cumulative distance and travel time, and the ETA is interpolated between them. ETAs therefore follow real speeds, not an average.
4. **Weather at the ETA.** From Open-Meteo, hourly, in mph and in/hr:
   - **Rain and snowfall** are totals for the *preceding* hour, so an ETA of 14:20 uses the 15:00 value, which covers 14:00–15:00.
   - **Wind** (sustained, 10 m) is linearly interpolated between the two hours around the ETA. Gusts are shown for information only.
5. **Risk** = the worst of wind (after the load rules), rain and snow. Each checkpoint also records:
   - which conditions set the level;
   - the weather-only level;
   - whether the load raised it.
6. **Recommendation.** Explained in [Recommendation](#recommendation).
7. **Heatmap.**
   - **Grid:** cells about 15 miles apart, covering 30 miles around every route, capped at 450 cells.
   - **Data:** 49 hourly frames, classified on the server with the same rules and the same load.
   - **Slider:** the browser only blends neighbouring frames for display, so dragging makes no network requests.

### Risk rules: band edges

The table in the brief overlaps or leaves gaps at a few edges. Here's how each edge is resolved; every one has a test in `backend/trips/tests/test_risk.py`.

| | Low | Moderate | High | Severe | No travel |
|---|---|---|---|---|---|
| Wind (mph) | <25 | [25, 35) | [35, 45) | [45, 55) | ≥55 |
| Rain (in/hr) | <0.10 | [0.10, 0.25) | [0.25, 0.50) | [0.50, 1.00] | >1.00 |
| Snow (in/hr) | <0.5 | [0.5, 1.0) | [1.0, 2.0) | [2.0, 3.0] | >3.0 |

- Bands include their lower edge. That resolves the doubled 0.25 and the gaps in the whole-number wind ranges (for example, 34.5 mph is Moderate).
- No Travel for rain and snow is strictly *greater than* the top value ("No Travel >1.00"), so exactly 1.00 in/hr is still Severe.
- The load rules use a strict ">": 30,000 lb at 50 mph is Severe and 30,001 lb is No Travel. The load rules only ever raise a level, and they only apply to wind.

### Recommendation

This is a strict order, with no tolerances:

0. Any route with a No Travel checkpoint is **excluded**. If every route is excluded, **nothing is recommended** and the app shows "Don't travel on this schedule".
1. Fewest Severe miles.
2. Fewest High miles.
3. Lowest average risk: the plain mean of checkpoint scores, where Low = 0 and No Travel = 4.
4. Shortest travel time.

**Counting miles:** each stretch between two checkpoints takes the *worse* level of its two ends. One Severe sample at mile 25 of a 25-mile grid therefore counts as 50 Severe miles. This is deliberately conservative, because a storm's edge between two samples isn't known.

**Missing forecasts:** a checkpoint without a forecast is shown as *Unknown*, counted as High for ranking, and reported as unknown miles. It is never treated as Low.

## Running locally

Keys go in `backend/.env`. Copy it from `.env.example`; the only required key is `TOMTOM_API_KEY`, which has a free tier at developer.tomtom.com. Open-Meteo needs no key.

```bash
# backend (http://localhost:8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python manage.py runserver

# frontend (http://localhost:5173, proxies /api to :8000)
cd frontend
npm install
npm run dev
```

Or with Docker:

```bash
cd backend  && docker compose --profile dev up --build
cd frontend && docker compose --profile dev up --build
```

### Tests

```bash
cd backend  && .venv/bin/pytest            # unit + API tests, providers faked
cd backend  && .venv/bin/pytest -m live    # smoke test against the real TomTom / Open-Meteo APIs
cd frontend && npm test && npm run typecheck
```

## Deploying (same server as other apps)

Each side has its own compose file, with a `prod` profile:

```bash
cd backend  && docker compose --profile prod up -d --build   # API on host port ${API_PORT:-8021}
cd frontend && docker compose --profile prod up -d --build   # site on host port ${WEB_PORT:-8020}
```

The frontend's nginx serves the app and proxies `/api/` to `API_UPSTREAM`, which defaults to `http://host.docker.internal:8021`. The browser therefore uses a single origin and no CORS setup is needed.

On the server:
- Point the domain at port 8020 in the host's reverse proxy.
- Add the domain to `DJANGO_ALLOWED_HOSTS` in `backend/.env`.

## Demo storm

Open `/plan?demo` to show a "Add a demo storm" option. It layers a clearly labeled synthetic storm on top of the live forecast so a walkthrough can show every risk level even on a calm day. To turn it off, set `ENABLE_SCENARIOS=false`.

## Limitations

- **No hours-of-service rules:** ETAs assume continuous driving with no mandatory rest breaks.
- **US only:** geocoding is restricted to `GEOCODE_COUNTRIES`.
- **Forecast range:** departures are limited to the next 72 h. Checkpoints past the end of the forecast become Unknown.
- **Free-tier quota:** each trip's heatmap uses up to about 450 Open-Meteo locations. Results are cached for 30 minutes per point, and requests are rate-limited per IP.
