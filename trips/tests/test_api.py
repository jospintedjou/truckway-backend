"""End-to-end API tests with the third-party providers replaced by fakes."""

import itertools
from datetime import datetime, timedelta

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from trips.providers import openmeteo, tomtom
from trips.providers.openmeteo import Series

DALLAS = {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797}
DENVER = {"label": "Denver, CO", "lat": 39.7392, "lon": -104.9903}


def _line(o, d, bend, n=200):
    pts = []
    for k in range(n + 1):
        f = k / n
        lat = o["lat"] + (d["lat"] - o["lat"]) * f + bend * f * (1 - f) * 4
        lon = o["lon"] + (d["lon"] - o["lon"]) * f
        pts.append((lat, lon))
    return pts


def fake_routes(locations, depart_at, load_lb, max_alternatives):
    o, d = DALLAS, DENVER
    out = []
    for i, bend in enumerate([0.0, 1.5, -1.5][: max_alternatives + 1]):
        length = 1_300_000 + i * 60_000
        duration = 13 * 3600 + i * 1800
        out.append(
            tomtom.RawRoute(
                points=_line(o, d, bend),
                length_m=length,
                duration_s=duration,
                timing=[(length / 2, duration / 2)],
            )
        )
    return out


def make_weather(windy_lat_above=None):
    """Calm everywhere, except 40 mph wind north of a latitude (to test load rules)."""

    def fetch(points):
        out = {}
        now = int(datetime.now().timestamp()) // 3600 * 3600 - 3600
        times = [now + h * 3600 for h in range(24 * 7)]
        for lat, lon in points:
            key = openmeteo.key_for(lat, lon)
            windy = windy_lat_above is not None and lat > windy_lat_above
            out[key] = Series(
                times=times,
                rain=[0.0] * len(times),
                snow=[0.0] * len(times),
                wind=[40.0 if windy else 8.0] * len(times),
                gust=[50.0 if windy else 12.0] * len(times),
                timezone="America/Chicago",
            )
        return out

    return fetch


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(tomtom, "truck_routes", fake_routes)
    return APIClient()


def departure(hours=2):
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def body(**overrides):
    data = {
        "origin": DALLAS,
        "destination": DENVER,
        "departure_local": departure(),
        "load_lb": 20_000,
        "interval_mi": 25,
    }
    data.update(overrides)
    return data


def test_plan_calm_weather_picks_fastest(client, monkeypatch):
    monkeypatch.setattr(openmeteo, "fetch", make_weather())
    resp = client.post("/api/trips/plan", body(), format="json")
    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert [r["id"] for r in data["routes"]] == ["A", "B", "C"]
    assert data["recommendation"]["route_id"] == "A"
    assert data["recommendation"]["reasons"][0].startswith("Shorter travel time")

    route = data["routes"][0]
    cps = route["checkpoints"]
    assert cps[0]["mile"] == 0
    assert cps[-1]["mile"] == route["distance_mi"]
    assert all(b["mile"] - a["mile"] <= 25.01 for a, b in itertools.pairwise(cps))
    # ETAs increase along the route and reach the route duration at the destination
    offsets = [c["eta_offset_s"] for c in cps]
    assert offsets == sorted(offsets)
    assert offsets[-1] == route["duration_s"]


def test_load_changes_the_outcome(client, monkeypatch):
    # 40 mph wind in the northern half of every route: High at 20k lb, Severe above 40k lb
    monkeypatch.setattr(openmeteo, "fetch", make_weather(windy_lat_above=36.5))
    light = client.post("/api/trips/plan", body(load_lb=20_000), format="json").json()
    heavy = client.post("/api/trips/plan", body(load_lb=42_000), format="json").json()

    assert light["routes"][0]["summary"]["severe_miles"] == 0
    assert light["routes"][0]["summary"]["high_miles"] > 0
    assert heavy["routes"][0]["summary"]["severe_miles"] > 0
    assert heavy["routes"][0]["summary"]["load_escalated_miles"] > 0
    escalated = [c for c in heavy["routes"][0]["checkpoints"] if c["load_escalated"]]
    assert escalated and escalated[0]["base_level"] == 2 and escalated[0]["level"] == 3


def test_validation_errors(client, monkeypatch):
    monkeypatch.setattr(openmeteo, "fetch", make_weather())
    assert client.post("/api/trips/plan", body(load_lb=0), format="json").status_code == 400
    assert client.post("/api/trips/plan", body(interval_mi=30), format="json").status_code == 400
    past = client.post("/api/trips/plan", body(departure_local=departure(-12)), format="json")
    assert past.status_code == 400
    assert "past" in past.json()["detail"]


def test_corridor_heatmap(client, monkeypatch):
    monkeypatch.setattr(openmeteo, "fetch", make_weather(windy_lat_above=36.5))
    plan = client.post("/api/trips/plan", body(load_lb=42_000), format="json").json()
    resp = client.get(f"/api/trips/{plan['trip']['id']}/corridor")
    assert resp.status_code == 200
    data = resp.json()
    n = len(data["cells"]["lat"])
    assert 0 < n <= 450
    assert data["hours"] == 49
    assert len(data["levels"]) == n * 49
    # load-aware: 40 mph cells are Severe for a 42,000 lb load
    assert 3 in data["levels"]


def test_corridor_unknown_trip(client):
    assert client.get("/api/trips/nope/corridor").status_code == 404


def test_storm_scenario_creates_risk(client, monkeypatch):
    monkeypatch.setattr(openmeteo, "fetch", make_weather())
    data = client.post("/api/trips/plan", body(load_lb=42_000, scenario="storm"), format="json").json()
    assert data["trip"]["scenario"] == "storm"
    worst = max(c["level"] for r in data["routes"] for c in r["checkpoints"])
    assert worst >= 3
