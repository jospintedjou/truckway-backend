import itertools

import pytest

from trips import geo
from trips.providers.openmeteo import Series


def test_checkpoint_miles_include_destination():
    assert geo.checkpoint_miles(60, 25) == [0, 25, 50, 60]
    assert geo.checkpoint_miles(50, 25) == [0, 25, 50]


def test_point_at_interpolates():
    pts = [(0.0, 0.0), (0.0, 1.0)]
    cum = geo.cumulative_miles(pts)
    lat, lon = geo.point_at(pts, cum, cum[-1] / 2)
    assert lat == 0
    assert lon == pytest.approx(0.5)


def test_time_profile_uses_guidance_speeds():
    # first 50 km slow (1 h), next 50 km fast (0.5 h)
    p = geo.TimeProfile.build([(50_000, 3600)], length_m=100_000, duration_s=5400)
    assert p.seconds_at(25_000) == pytest.approx(1800)
    assert p.seconds_at(75_000) == pytest.approx(3600 + 900)
    assert p.seconds_at(100_000) == 5400


def test_time_profile_ignores_bad_guidance():
    p = geo.TimeProfile.build([(80_000, 100), (120_000, 9999)], length_m=100_000, duration_s=5400)
    assert p.seconds_at(100_000) == 5400
    assert p.seconds_at(90_000) >= 100


def test_corridor_grid_stays_near_route_and_respects_cap():
    route = [(32.78, -96.80), (35.2, -101.8), (39.74, -104.99)]  # Dallas -> Amarillo -> Denver
    dense = []
    for (a1, o1), (a2, o2) in itertools.pairwise(route):
        for k in range(50):
            f = k / 50
            dense.append((a1 + (a2 - a1) * f, o1 + (o2 - o1) * f))
    grid = geo.corridor_grid(dense, radius_mi=30, max_cells=450)
    assert 0 < len(grid.lats) <= 450
    for lat, lon in zip(grid.lats, grid.lons, strict=False):
        nearest = min(geo.haversine_mi(lat, lon, a, o) for a, o in dense)
        assert nearest <= 31


H = 3600


def series():
    return Series(
        times=[0, H, 2 * H],
        rain=[0.0, 0.2, 0.6],
        snow=[0.0, 0.0, 0.0],
        wind=[10.0, 20.0, 40.0],
        gust=[15.0, 25.0, 50.0],
        timezone="America/Chicago",
    )


def test_series_precip_uses_hour_ending_after_eta():
    # 01:20 -> hour 01:00-02:00, reported at 02:00
    w = series().at(H + 20 * 60)
    assert w.rain_in_hr == 0.6


def test_series_wind_is_interpolated():
    w = series().at(H + 30 * 60)
    assert w.wind_mph == pytest.approx(30.0)


def test_series_exact_hour():
    w = series().at(H)
    assert w.rain_in_hr == 0.2
    assert w.wind_mph == 20.0


def test_series_out_of_range_is_unknown():
    assert series().at(3 * H) is None
