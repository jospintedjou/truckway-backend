"""Geometry helpers: distances, sampling along a route, ETAs and the corridor grid."""

from __future__ import annotations

import bisect
import itertools
import math
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_MI = 3958.8
METERS_PER_MILE = 1609.344


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(a)))


def cumulative_miles(points: list[tuple[float, float]]) -> list[float]:
    out = [0.0]
    for (la1, lo1), (la2, lo2) in itertools.pairwise(points):
        out.append(out[-1] + haversine_mi(la1, lo1, la2, lo2))
    return out


def point_at(points: list[tuple[float, float]], cum: list[float], mile: float) -> tuple[float, float]:
    """Interpolated (lat, lon) at a distance along the polyline."""
    if mile <= 0:
        return points[0]
    if mile >= cum[-1]:
        return points[-1]
    i = bisect.bisect_right(cum, mile) - 1
    seg = cum[i + 1] - cum[i]
    f = 0.0 if seg == 0 else (mile - cum[i]) / seg
    (la1, lo1), (la2, lo2) = points[i], points[i + 1]
    return la1 + (la2 - la1) * f, lo1 + (lo2 - lo1) * f


def checkpoint_miles(total_mi: float, interval_mi: float) -> list[float]:
    """0, interval, 2*interval, ... and always the destination."""
    miles = []
    m = 0.0
    while m < total_mi - 1e-6:
        miles.append(m)
        m += interval_mi
    miles.append(total_mi)
    return miles


@dataclass
class TimeProfile:
    """Maps distance along the route to seconds since departure.

    Built from the routing provider's guidance (cumulative offset and travel time
    at each instruction), so ETAs follow real speeds rather than a constant average.
    """

    offsets_m: list[float]
    times_s: list[float]

    @classmethod
    def build(cls, pairs: list[tuple[float, float]], length_m: float, duration_s: float) -> TimeProfile:
        cleaned = sorted({(0.0, 0.0), (float(length_m), float(duration_s)), *pairs})
        offsets, times = [], []
        for off, t in cleaned:
            if off > length_m or (offsets and off <= offsets[-1]):
                continue
            # travel time never decreases along the route
            t = max(t, times[-1]) if times else t
            offsets.append(off)
            times.append(t)
        return cls(offsets, times)

    def seconds_at(self, offset_m: float) -> float:
        if offset_m <= self.offsets_m[0]:
            return self.times_s[0]
        if offset_m >= self.offsets_m[-1]:
            return self.times_s[-1]
        i = bisect.bisect_right(self.offsets_m, offset_m) - 1
        span = self.offsets_m[i + 1] - self.offsets_m[i]
        f = (offset_m - self.offsets_m[i]) / span
        return self.times_s[i] + (self.times_s[i + 1] - self.times_s[i]) * f


def decimate(points: list[tuple[float, float]], cum: list[float], min_gap_mi: float) -> list[int]:
    """Indexes of points at least `min_gap_mi` apart (first and last always kept)."""
    keep = [0]
    for i in range(1, len(points) - 1):
        if cum[i] - cum[keep[-1]] >= min_gap_mi:
            keep.append(i)
    if len(points) > 1:
        keep.append(len(points) - 1)
    return keep


@dataclass
class Grid:
    lats: list[float]
    lons: list[float]
    dlat: float
    dlon: float


def corridor_grid(
    route_points: list[tuple[float, float]],
    radius_mi: float = 30.0,
    target_spacing_mi: float = 15.0,
    max_cells: int = 450,
) -> Grid:
    """Square-ish cells whose centers lie within `radius_mi` of any route point."""
    pts = np.asarray(route_points, dtype=float)
    mid_lat = float(pts[:, 0].mean())
    cos_lat = max(0.2, math.cos(math.radians(mid_lat)))
    pad_lat = radius_mi / 69.0
    pad_lon = radius_mi / (69.0 * cos_lat)
    lat_min, lat_max = pts[:, 0].min() - pad_lat, pts[:, 0].max() + pad_lat
    lon_min, lon_max = pts[:, 1].min() - pad_lon, pts[:, 1].max() + pad_lon

    rad = np.radians(pts)
    spacing = target_spacing_mi
    while True:
        dlat = spacing / 69.0
        dlon = spacing / (69.0 * cos_lat)
        glat = np.arange(lat_min + dlat / 2, lat_max, dlat)
        glon = np.arange(lon_min + dlon / 2, lon_max, dlon)
        mlat, mlon = np.meshgrid(glat, glon, indexing="ij")
        cand = np.column_stack([mlat.ravel(), mlon.ravel()])
        crad = np.radians(cand)
        # equirectangular distance is accurate enough at these scales
        x = (crad[:, None, 1] - rad[None, :, 1]) * np.cos((crad[:, None, 0] + rad[None, :, 0]) / 2)
        y = crad[:, None, 0] - rad[None, :, 0]
        dist = EARTH_RADIUS_MI * np.sqrt(x * x + y * y).min(axis=1)
        inside = cand[dist <= radius_mi]
        if len(inside) <= max_cells:
            return Grid(
                lats=[round(v, 4) for v in inside[:, 0]],
                lons=[round(v, 4) for v in inside[:, 1]],
                dlat=dlat,
                dlon=dlon,
            )
        spacing *= 1.2
