"""Weather risk classification.

Pure functions, no I/O. Bands include their lower edge and exclude their upper
edge ([a, b)), except the top No Travel band for rain and snow, which starts
strictly above the Severe band (the spec writes "Severe 0.50-1.00, No Travel >1.00").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Level(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title().replace("No Travel", "No travel")


# Load rule thresholds (lb). Strictly greater than, as the spec says ">30,000 lb".
LOAD_NO_TRAVEL_LB = 30_000
LOAD_SEVERE_LB = 40_000


def classify_wind(mph: float) -> Level:
    if mph < 25:
        return Level.LOW
    if mph < 35:
        return Level.MODERATE
    if mph < 45:
        return Level.HIGH
    if mph < 55:
        return Level.SEVERE
    return Level.NO_TRAVEL


def classify_rain(in_per_hr: float) -> Level:
    if in_per_hr < 0.10:
        return Level.LOW
    if in_per_hr < 0.25:
        return Level.MODERATE
    if in_per_hr < 0.50:
        return Level.HIGH
    if in_per_hr <= 1.00:
        return Level.SEVERE
    return Level.NO_TRAVEL


def classify_snow(in_per_hr: float) -> Level:
    if in_per_hr < 0.5:
        return Level.LOW
    if in_per_hr < 1.0:
        return Level.MODERATE
    if in_per_hr < 2.0:
        return Level.HIGH
    if in_per_hr <= 3.0:
        return Level.SEVERE
    return Level.NO_TRAVEL


def load_adjusted_wind(mph: float, load_lb: float) -> Level:
    """Wind level after the load rules. Load rules only ever raise the level."""
    base = classify_wind(mph)
    if mph >= 55:
        return Level.NO_TRAVEL
    if 45 <= mph < 55 and load_lb > LOAD_NO_TRAVEL_LB:
        return Level.NO_TRAVEL
    if 35 <= mph < 45 and load_lb > LOAD_SEVERE_LB:
        return max(base, Level.SEVERE)
    return base


@dataclass(frozen=True)
class Weather:
    wind_mph: float
    gust_mph: float
    rain_in_hr: float
    snow_in_hr: float


@dataclass(frozen=True)
class Assessment:
    level: Level
    base_level: Level  # weather only, before load rules
    drivers: list[str] = field(default_factory=list)  # conditions that set the final level
    load_escalated: bool = False


def assess(weather: Weather, load_lb: float) -> Assessment:
    """Checkpoint risk = worst of wind (load-adjusted), rain and snow."""
    wind_base = classify_wind(weather.wind_mph)
    wind_final = load_adjusted_wind(weather.wind_mph, load_lb)
    rain = classify_rain(weather.rain_in_hr)
    snow = classify_snow(weather.snow_in_hr)

    base_level = max(wind_base, rain, snow)
    level = max(wind_final, rain, snow)

    drivers: list[str] = []
    if level > Level.LOW:
        if wind_final == level:
            drivers.append("wind")
        if rain == level:
            drivers.append("rain")
        if snow == level:
            drivers.append("snow")

    return Assessment(
        level=level,
        base_level=base_level,
        drivers=drivers,
        load_escalated=level > base_level,
    )
