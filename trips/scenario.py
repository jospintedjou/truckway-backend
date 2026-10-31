"""Optional demo scenario: a synthetic storm layered on top of the live forecast.

Only active when ENABLE_SCENARIOS is on and the request asks for `scenario=storm`.
The UI labels it clearly; it exists so a walkthrough can show every risk level
even when the real weather is calm.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .geo import haversine_mi
from .risk import Weather


@dataclass
class Storm:
    lat: float
    lon: float
    peak_t: float  # epoch seconds when the storm is strongest
    radius_mi: float = 90.0
    speed_mph: float = 18.0  # drifts east-north-east
    peak_wind_mph: float = 48.0
    peak_snow_in_hr: float = 2.4

    def center_at(self, t: float) -> tuple[float, float]:
        hours = (t - self.peak_t) / 3600
        dist = self.speed_mph * hours
        dlat = dist * math.sin(math.radians(25)) / 69.0
        dlon = dist * math.cos(math.radians(25)) / (69.0 * max(0.2, math.cos(math.radians(self.lat))))
        return self.lat + dlat, self.lon + dlon

    def apply(self, lat: float, lon: float, t: float, w: Weather | None) -> Weather | None:
        if w is None:
            return None
        clat, clon = self.center_at(t)
        d = haversine_mi(lat, lon, clat, clon)
        if d >= self.radius_mi:
            return w
        space = 1 - d / self.radius_mi
        time = math.exp(-((((t - self.peak_t) / 3600) / 8) ** 2))
        f = space * time
        return Weather(
            wind_mph=w.wind_mph + self.peak_wind_mph * f,
            gust_mph=w.gust_mph + self.peak_wind_mph * 1.3 * f,
            rain_in_hr=w.rain_in_hr,
            snow_in_hr=w.snow_in_hr + self.peak_snow_in_hr * f * f,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def storm_for(points: list[tuple[float, float]], times: list[float]) -> Storm:
    """Place the storm 45% along the primary route, peaking when the truck gets there."""
    i = int(len(points) * 0.45)
    lat, lon = points[i]
    return Storm(lat=lat, lon=lon, peak_t=times[i])
