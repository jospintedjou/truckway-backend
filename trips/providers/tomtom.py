"""TomTom adapter: geocoding and truck routing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from django.conf import settings

LB_TO_KG = 0.45359237


class ProviderError(Exception):
    """A third-party API failed or returned something unusable."""


class NoRouteError(ProviderError):
    pass


@dataclass
class Place:
    label: str
    lat: float
    lon: float


@dataclass
class RawRoute:
    points: list[tuple[float, float]]  # (lat, lon)
    length_m: float
    duration_s: float
    timing: list[tuple[float, float]]  # (route offset m, seconds since departure)


def _client() -> httpx.Client:
    return httpx.Client(base_url=settings.TOMTOM_BASE_URL, timeout=20.0)


def _key() -> str:
    if not settings.TOMTOM_API_KEY:
        raise ProviderError("TOMTOM_API_KEY is not set")
    return settings.TOMTOM_API_KEY


def geocode(query: str, limit: int = 6) -> list[Place]:
    params = {
        "key": _key(),
        "limit": limit,
        "countrySet": settings.GEOCODE_COUNTRIES,
        "typeahead": "true",
        "idxSet": "Geo,PAD,Addr,Str",
        "language": "en-US",
    }
    with _client() as client:
        resp = client.get(f"/search/2/search/{quote(query)}.json", params=params)
    if resp.status_code != 200:
        raise ProviderError(f"Geocoding failed ({resp.status_code})")
    places = []
    for item in resp.json().get("results", []):
        pos = item.get("position") or {}
        addr = item.get("address") or {}
        label = addr.get("freeformAddress") or item.get("poi", {}).get("name")
        if label and "lat" in pos and "lon" in pos:
            places.append(Place(label=label, lat=pos["lat"], lon=pos["lon"]))
    return places


def truck_routes(
    locations: list[tuple[float, float]],
    depart_at: datetime | None,
    load_lb: float,
    max_alternatives: int,
) -> list[RawRoute]:
    """Truck routes through `locations` (origin, optional vias, destination)."""
    loc = ":".join(f"{lat:.6f},{lon:.6f}" for lat, lon in locations)
    gross_kg = round((settings.TRUCK_TARE_LB + load_lb) * LB_TO_KG)
    params = {
        "key": _key(),
        "travelMode": "truck",
        "vehicleWeight": gross_kg,
        "vehicleCommercial": "true",
        "maxAlternatives": max_alternatives,
        "traffic": "true",
        "instructionsType": "coded",
        "routeRepresentation": "polyline",
    }
    if depart_at is not None:
        params["departAt"] = depart_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _client() as client:
        resp = client.get(f"/routing/1/calculateRoute/{loc}/json", params=params)
    if resp.status_code == 400:
        raise NoRouteError("No truck route found between these places")
    if resp.status_code != 200:
        raise ProviderError(f"Routing failed ({resp.status_code})")
    return [_parse_route(r) for r in resp.json().get("routes", [])]


def _parse_route(route: dict) -> RawRoute:
    points: list[tuple[float, float]] = []
    for leg in route.get("legs", []):
        for p in leg.get("points", []):
            pt = (p["latitude"], p["longitude"])
            if not points or points[-1] != pt:
                points.append(pt)
    summary = route["summary"]
    timing = [
        (float(ins["routeOffsetInMeters"]), float(ins["travelTimeInSeconds"]))
        for ins in route.get("guidance", {}).get("instructions", [])
        if "routeOffsetInMeters" in ins and "travelTimeInSeconds" in ins
    ]
    return RawRoute(
        points=points,
        length_m=float(summary["lengthInMeters"]),
        duration_s=float(summary["travelTimeInSeconds"]),
        timing=timing,
    )
