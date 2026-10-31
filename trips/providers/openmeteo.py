"""Open-Meteo adapter: hourly forecasts for many points, cached per point.

Units requested: wind in mph, precipitation and snowfall in inches.
Hourly `rain` and `snowfall` are totals for the *preceding* hour, so they are
read as an in/hr rate for the hour that ends at that timestamp.
"""

from __future__ import annotations

import bisect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from django.conf import settings
from django.core.cache import cache

from ..risk import Weather
from .tomtom import ProviderError

HOURLY = "rain,snowfall,wind_speed_10m,wind_gusts_10m"
BATCH = 50
CACHE_SECONDS = 30 * 60


@dataclass
class Series:
    times: list[int]  # epoch seconds, hourly
    rain: list[float | None]
    snow: list[float | None]
    wind: list[float | None]
    gust: list[float | None]
    timezone: str

    def at(self, t: float) -> Weather | None:
        """Weather at an arbitrary instant.

        Precipitation: the hourly total for the hour containing t (the next
        timestamp at or after t). Wind: linear interpolation between hours.
        """
        times = self.times
        if not times or t < times[0] - 3600 or t > times[-1]:
            return None
        hi = bisect.bisect_left(times, t)
        hi = min(hi, len(times) - 1)
        lo = hi if times[hi] == t or hi == 0 else hi - 1

        rain, snow = self.rain[hi], self.snow[hi]
        w_lo, w_hi = self.wind[lo], self.wind[hi]
        g_lo, g_hi = self.gust[lo], self.gust[hi]
        if None in (rain, snow, w_lo, w_hi):
            return None
        f = 0.0 if hi == lo else (t - times[lo]) / (times[hi] - times[lo])
        f = min(max(f, 0.0), 1.0)
        wind = w_lo + (w_hi - w_lo) * f
        gust = wind if g_lo is None or g_hi is None else g_lo + (g_hi - g_lo) * f
        return Weather(wind_mph=wind, gust_mph=gust, rain_in_hr=rain, snow_in_hr=snow)


def key_for(lat: float, lon: float) -> tuple[float, float]:
    """Points are rounded to 0.01 deg (~1 km) so nearby requests share a cache entry."""
    return round(lat, 2), round(lon, 2)


def _cache_key(pt: tuple[float, float]) -> str:
    return f"om:{pt[0]:.2f}:{pt[1]:.2f}"


def fetch(points: list[tuple[float, float]]) -> dict[tuple[float, float], Series]:
    """Forecast series for each point (keys are rounded with `key_for`)."""
    wanted = list(dict.fromkeys(key_for(lat, lon) for lat, lon in points))
    out: dict[tuple[float, float], Series] = {}
    missing = []
    for pt in wanted:
        hit = cache.get(_cache_key(pt))
        if hit is not None:
            out[pt] = hit
        else:
            missing.append(pt)

    batches = [missing[i : i + BATCH] for i in range(0, len(missing), BATCH)]
    if batches:
        fresh: dict[tuple[float, float], Series] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(_fetch_batch, batches):
                fresh.update(result)
        cache.set_many({_cache_key(pt): s for pt, s in fresh.items()}, CACHE_SECONDS)
        out.update(fresh)
    return out


def _fetch_batch(batch: list[tuple[float, float]]) -> dict[tuple[float, float], Series]:
    params = {
        "latitude": ",".join(f"{lat:.2f}" for lat, _ in batch),
        "longitude": ",".join(f"{lon:.2f}" for _, lon in batch),
        "hourly": HOURLY,
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timeformat": "unixtime",
        "timezone": "auto",
        "forecast_days": settings.FORECAST_DAYS,
    }
    if settings.OPEN_METEO_API_KEY:
        params["apikey"] = settings.OPEN_METEO_API_KEY
    try:
        resp = httpx.get(settings.OPEN_METEO_URL, params=params, timeout=20.0)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Weather service unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise ProviderError(f"Weather request failed ({resp.status_code})")
    data = resp.json()
    items = data if isinstance(data, list) else [data]
    result = {}
    for pt, item in zip(batch, items, strict=False):
        hourly = item.get("hourly") or {}
        result[pt] = Series(
            times=[int(t) for t in hourly.get("time", [])],
            rain=hourly.get("rain", []),
            snow=hourly.get("snowfall", []),
            wind=hourly.get("wind_speed_10m", []),
            gust=hourly.get("wind_gusts_10m", []),
            timezone=item.get("timezone", "UTC"),
        )
    return result
