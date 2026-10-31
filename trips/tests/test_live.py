"""Smoke tests against the real APIs. Not run by default.

    pytest -m live

Needs TOMTOM_API_KEY in backend/.env. Checks the provider contracts we rely on.
"""

from datetime import datetime, timedelta

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.live


def test_live_geocode():
    resp = APIClient().get("/api/geocode", {"q": "Dallas, TX"})
    assert resp.status_code == 200, resp.content
    assert resp.json()["results"], "TomTom returned no places"


def test_live_plan_and_corridor():
    dep = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    body = {
        "origin": {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797},
        "destination": {"label": "Oklahoma City, OK", "lat": 35.4676, "lon": -97.5164},
        "departure_local": dep,
        "load_lb": 35_000,
        "interval_mi": 25,
    }
    client = APIClient()
    resp = client.post("/api/trips/plan", body, format="json")
    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert 1 <= len(data["routes"]) <= 3
    assert all(c["weather"] is not None for r in data["routes"] for c in r["checkpoints"])

    corridor = client.get(f"/api/trips/{data['trip']['id']}/corridor")
    assert corridor.status_code == 200
    assert corridor.json()["hours"] == 49
