from django.utils import timezone as dj_timezone
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from . import services
from .providers import tomtom
from .serializers import PlanRequestSerializer


class IPThrottle(SimpleRateThrottle):
    """Per-IP rate limit (no user accounts in this app). Protects API quotas."""

    scope = "ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PlanThrottle(IPThrottle):
    scope = "plan"


def _error(message: str, status: int) -> Response:
    return Response({"detail": message}, status=status)


@api_view(["GET"])
def health(request):
    return Response({"status": "ok", "time": dj_timezone.now().isoformat()})


@api_view(["GET"])
@throttle_classes([IPThrottle])
def geocode(request):
    query = (request.query_params.get("q") or "").strip()
    if len(query) < 3:
        return Response({"results": []})
    try:
        places = tomtom.geocode(query)
    except tomtom.ProviderError as exc:
        return _error(str(exc), 502)
    return Response({"results": [p.__dict__ for p in places]})


def _resolve_place(data: dict) -> tomtom.Place:
    if "lat" in data:
        return tomtom.Place(label=data["label"], lat=data["lat"], lon=data["lon"])
    found = tomtom.geocode(data["label"], limit=1)
    if not found:
        raise services.PlanError(f'Couldn\'t find "{data["label"]}". Pick a place from the list.', 422)
    return found[0]


@api_view(["POST"])
@throttle_classes([PlanThrottle])
def plan(request):
    ser = PlanRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    try:
        req = services.PlanRequest(
            origin=_resolve_place(data["origin"]),
            destination=_resolve_place(data["destination"]),
            departure_local=data["departure_local"].replace(tzinfo=None),
            load_lb=data["load_lb"],
            interval_mi=int(data["interval_mi"]),
            scenario=data.get("scenario"),
        )
        return Response(services.plan_trip(req))
    except services.PlanError as exc:
        return _error(exc.message, exc.status)
    except tomtom.NoRouteError as exc:
        return _error(str(exc), 422)
    except tomtom.ProviderError as exc:
        return _error(str(exc), 502)


@api_view(["GET"])
@throttle_classes([IPThrottle])
def corridor(request, trip_id: str):
    try:
        return Response(services.corridor(trip_id))
    except services.PlanError as exc:
        return _error(exc.message, exc.status)
    except tomtom.ProviderError as exc:
        return _error(str(exc), 502)
