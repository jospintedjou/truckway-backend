"""Trip planning: routes -> checkpoints at ETAs -> weather -> risk -> recommendation."""

from __future__ import annotations

import math
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache

from . import geo
from .providers import openmeteo, tomtom
from .providers.tomtom import Place, RawRoute
from .ranking import CheckpointRisk, recommend, summarize
from .risk import Weather, assess
from .scenario import Storm, storm_for

ROUTE_IDS = "ABC"
TRIP_CACHE_SECONDS = 2 * 60 * 60
CORRIDOR_HOURS = 48


class PlanError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class PlanRequest:
    origin: Place
    destination: Place
    departure_local: datetime  # naive, local time at the origin
    load_lb: float
    interval_mi: int
    scenario: str | None = None


# ---------- routes ----------


def _distinct(candidate: RawRoute, existing: list[RawRoute]) -> bool:
    c_cum = geo.cumulative_miles(candidate.points)
    c_mid = geo.point_at(candidate.points, c_cum, c_cum[-1] / 2)
    for r in existing:
        r_cum = geo.cumulative_miles(r.points)
        r_mid = geo.point_at(r.points, r_cum, r_cum[-1] / 2)
        same_length = abs(candidate.length_m - r.length_m) / max(r.length_m, 1) < 0.02
        if same_length and geo.haversine_mi(*c_mid, *r_mid) < 3:
            return False
    return True


def _via_points(o: Place, d: Place) -> list[tuple[float, float]]:
    """Two points either side of the straight line, used to force extra alternatives."""
    mid_lat, mid_lon = (o.lat + d.lat) / 2, (o.lon + d.lon) / 2
    cos_lat = math.cos(math.radians(mid_lat))
    dx, dy = (d.lon - o.lon) * cos_lat, d.lat - o.lat
    norm = math.hypot(dx, dy) or 1.0
    px, py = -dy / norm, dx / norm
    offset_mi = min(150.0, 0.15 * geo.haversine_mi(o.lat, o.lon, d.lat, d.lon))
    off_deg = offset_mi / 69.0
    return [(mid_lat + py * off_deg * s, mid_lon + px * off_deg * s / max(cos_lat, 0.2)) for s in (1, -1)]


def find_routes(o: Place, d: Place, depart_at: datetime | None, load_lb: float) -> list[RawRoute]:
    routes = tomtom.truck_routes([(o.lat, o.lon), (d.lat, d.lon)], depart_at, load_lb, max_alternatives=2)
    if not routes:
        raise PlanError("No truck route found between these places", 422)
    if len(routes) < 3:
        vias = _via_points(o, d)

        def via_route(via):
            try:
                found = tomtom.truck_routes([(o.lat, o.lon), via, (d.lat, d.lon)], depart_at, load_lb, 0)
                return found[0] if found else None
            except tomtom.ProviderError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            for extra in pool.map(via_route, vias):
                if extra is not None and len(routes) < 3 and _distinct(extra, routes):
                    routes.append(extra)
    return routes[:3]


# ---------- planning ----------


def _origin_timezone(o: Place) -> str:
    series = openmeteo.fetch([(o.lat, o.lon)])
    return next(iter(series.values())).timezone


def _resolve_departure(req: PlanRequest, tz_name: str) -> datetime:
    departure = req.departure_local.replace(tzinfo=ZoneInfo(tz_name))
    now = datetime.now(UTC)
    if departure < now - timedelta(minutes=15):
        raise PlanError("Departure time is in the past at the origin")
    if departure > now + timedelta(hours=settings.MAX_DEPARTURE_HOURS):
        raise PlanError(f"Departure must be within the next {settings.MAX_DEPARTURE_HOURS} hours (forecast range)")
    return max(departure, now)


def _weather_json(w: Weather | None) -> dict | None:
    if w is None:
        return None
    return {
        "wind_mph": round(w.wind_mph, 1),
        "gust_mph": round(w.gust_mph, 1),
        "rain_in_hr": round(w.rain_in_hr, 3),
        "snow_in_hr": round(w.snow_in_hr, 2),
    }


def plan_trip(req: PlanRequest) -> dict:
    tz_name = _origin_timezone(req.origin)
    departure = _resolve_departure(req, tz_name)
    dep_epoch = departure.timestamp()
    depart_at = departure if departure > datetime.now(UTC) + timedelta(minutes=1) else None

    raw_routes = find_routes(req.origin, req.destination, depart_at, req.load_lb)

    # Geometry and checkpoints (positions and ETAs) for every route
    prepared = []
    for rid, raw in zip(ROUTE_IDS, raw_routes, strict=False):
        cum = geo.cumulative_miles(raw.points)
        scale = (raw.length_m / geo.METERS_PER_MILE) / cum[-1] if cum[-1] > 0 else 1.0
        cum = [c * scale for c in cum]  # agree with the provider's distance
        profile = geo.TimeProfile.build(raw.timing, raw.length_m, raw.duration_s)

        cps = []
        for mile in geo.checkpoint_miles(cum[-1], req.interval_mi):
            lat, lon = geo.point_at(raw.points, cum, mile)
            offset = profile.seconds_at(mile * geo.METERS_PER_MILE)
            cps.append({"mile": mile, "lat": lat, "lon": lon, "offset": offset})

        keep = geo.decimate(raw.points, cum, 0.3)
        geometry = [
            [
                round(raw.points[i][1], 5),
                round(raw.points[i][0], 5),
                round(profile.seconds_at(cum[i] * geo.METERS_PER_MILE)),
            ]
            for i in keep
        ]
        coarse = [raw.points[i] for i in geo.decimate(raw.points, cum, 5.0)]
        prepared.append((rid, raw, cum, profile, cps, geometry, coarse))

    storm: Storm | None = None
    if req.scenario == "storm" and settings.ENABLE_SCENARIOS:
        _, raw0, cum0, prof0, _, _, _ = prepared[0]
        times = [dep_epoch + prof0.seconds_at(c * geo.METERS_PER_MILE) for c in cum0]
        storm = storm_for(raw0.points, times)

    all_points = [(cp["lat"], cp["lon"]) for p in prepared for cp in p[4]]
    forecast = openmeteo.fetch(all_points)

    routes_json = []
    summaries = []
    for rid, raw, cum, _profile, cps, geometry, _ in prepared:
        risks = []
        cps_json = []
        for cp in cps:
            eta = dep_epoch + cp["offset"]
            series = forecast.get(openmeteo.key_for(cp["lat"], cp["lon"]))
            weather = series.at(eta) if series else None
            if storm is not None:
                weather = storm.apply(cp["lat"], cp["lon"], eta, weather)
            a = assess(weather, req.load_lb) if weather else None
            risks.append(
                CheckpointRisk(
                    mile=cp["mile"],
                    level=a.level if a else None,
                    base_level=a.base_level if a else None,
                    drivers=a.drivers if a else [],
                )
            )
            cps_json.append(
                {
                    "mile": round(cp["mile"], 1),
                    "lat": round(cp["lat"], 5),
                    "lon": round(cp["lon"], 5),
                    "eta": datetime.fromtimestamp(eta, UTC).isoformat(),
                    "eta_offset_s": round(cp["offset"]),
                    "tz": series.timezone if series else tz_name,
                    "weather": _weather_json(weather),
                    "level": int(a.level) if a else None,
                    "base_level": int(a.base_level) if a else None,
                    "drivers": a.drivers if a else [],
                    "load_escalated": a.load_escalated if a else False,
                }
            )
        summary = summarize(rid, risks, raw.duration_s, cum[-1])
        summaries.append(summary)
        routes_json.append(
            {
                "id": rid,
                "distance_mi": round(cum[-1], 1),
                "duration_s": round(raw.duration_s),
                "arrival": datetime.fromtimestamp(dep_epoch + raw.duration_s, UTC).isoformat(),
                "geometry": geometry,
                "checkpoints": cps_json,
                "summary": summary.as_dict(),
            }
        )

    trip_id = uuid.uuid4().hex
    cache.set(
        f"trip:{trip_id}",
        {
            "departure": dep_epoch,
            "load_lb": req.load_lb,
            "points": [pt for p in prepared for pt in p[6]],
            "storm": storm.to_dict() if storm else None,
        },
        TRIP_CACHE_SECONDS,
    )

    return {
        "trip": {
            "id": trip_id,
            "origin": {"label": req.origin.label, "lat": req.origin.lat, "lon": req.origin.lon},
            "destination": {"label": req.destination.label, "lat": req.destination.lat, "lon": req.destination.lon},
            "departure": departure.astimezone(UTC).isoformat(),
            "timezone": tz_name,
            "load_lb": req.load_lb,
            "interval_mi": req.interval_mi,
            "scenario": "storm" if storm else None,
        },
        "routes": routes_json,
        "recommendation": recommend(summaries),
    }


# ---------- corridor heatmap ----------


def corridor(trip_id: str) -> dict:
    cached = cache.get(f"corridor:{trip_id}")
    if cached is not None:
        return cached
    ctx = cache.get(f"trip:{trip_id}")
    if ctx is None:
        raise PlanError("This trip has expired. Plan it again.", 404)

    grid = geo.corridor_grid(ctx["points"], radius_mi=settings.CORRIDOR_RADIUS_MI)
    cells = list(zip(grid.lats, grid.lons, strict=False))
    forecast = openmeteo.fetch(cells)
    storm = Storm(**ctx["storm"]) if ctx["storm"] else None
    load_lb = ctx["load_lb"]

    start = int(ctx["departure"] // 3600 * 3600)
    hours = CORRIDOR_HOURS + 1
    n = len(cells)
    levels = [-1] * (hours * n)
    drivers = [0] * (hours * n)  # 0 none, 1 wind, 2 rain, 3 snow (first condition setting the level)
    wind = [0] * (hours * n)
    rain = [0] * (hours * n)
    snow = [0] * (hours * n)
    driver_codes = {"wind": 1, "rain": 2, "snow": 3}

    for i, (lat, lon) in enumerate(cells):
        series = forecast.get(openmeteo.key_for(lat, lon))
        for h in range(hours):
            t = start + h * 3600
            w = series.at(t) if series else None
            if storm is not None:
                w = storm.apply(lat, lon, t, w)
            if w is None:
                continue
            k = h * n + i
            a = assess(w, load_lb)
            levels[k] = int(a.level)
            drivers[k] = driver_codes[a.drivers[0]] if a.drivers else 0
            wind[k] = round(w.wind_mph)
            rain[k] = round(w.rain_in_hr * 100)
            snow[k] = round(w.snow_in_hr * 10)

    result = {
        "trip_id": trip_id,
        "start": datetime.fromtimestamp(start, UTC).isoformat(),
        "hours": hours,
        "load_lb": load_lb,
        "cells": {"lat": grid.lats, "lon": grid.lons, "dlat": grid.dlat, "dlon": grid.dlon},
        "levels": levels,
        "drivers": drivers,
        "wind_mph": wind,
        "rain_in_hr_x100": rain,
        "snow_in_hr_x10": snow,
    }
    cache.set(f"corridor:{trip_id}", result, TRIP_CACHE_SECONDS)
    return result
