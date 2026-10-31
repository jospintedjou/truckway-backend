"""Route summaries and the recommendation.

Miles per level: each stretch between two consecutive checkpoints takes the
worse level of its two ends (conservative: we do not know where a storm ends
between two samples).

Unknown weather (missing forecast) is never treated as Low: it counts as High
for ranking and is reported separately as unknown miles.

Ranking, strict order, no tolerances:
  0. Routes with any No Travel checkpoint are not eligible.
  1. Fewest Severe miles
  2. Fewest High miles
  3. Lowest average risk (plain mean of checkpoint scores 0-4)
  4. Shortest travel time
If no route is eligible there is no recommendation.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .risk import Level

UNKNOWN_AS = Level.HIGH


@dataclass
class CheckpointRisk:
    mile: float
    level: Level | None  # None = unknown weather
    base_level: Level | None
    drivers: list[str] = field(default_factory=list)


@dataclass
class RouteSummary:
    route_id: str
    duration_s: float
    distance_mi: float
    miles: dict[str, float]
    unknown_miles: float
    load_escalated_miles: float
    avg_risk: float
    eligible: bool
    blocked_at: dict | None

    def sort_key(self) -> tuple:
        return (
            round(self.miles["severe"], 3),
            round(self.miles["high"], 3),
            round(self.avg_risk, 6),
            self.duration_s,
        )

    def as_dict(self) -> dict:
        return {
            "distance_mi": round(self.distance_mi, 1),
            "duration_s": round(self.duration_s),
            "no_travel_miles": round(self.miles["no_travel"], 1),
            "severe_miles": round(self.miles["severe"], 1),
            "high_miles": round(self.miles["high"], 1),
            "moderate_miles": round(self.miles["moderate"], 1),
            "low_miles": round(self.miles["low"], 1),
            "unknown_miles": round(self.unknown_miles, 1),
            "load_escalated_miles": round(self.load_escalated_miles, 1),
            "avg_risk": round(self.avg_risk, 2),
            "eligible": self.eligible,
            "blocked_at": self.blocked_at,
        }


_KEYS = {
    Level.LOW: "low",
    Level.MODERATE: "moderate",
    Level.HIGH: "high",
    Level.SEVERE: "severe",
    Level.NO_TRAVEL: "no_travel",
}


def _effective(level: Level | None) -> Level:
    return UNKNOWN_AS if level is None else level


def summarize(route_id: str, checkpoints: list[CheckpointRisk], duration_s: float, distance_mi: float) -> RouteSummary:
    miles = {key: 0.0 for key in _KEYS.values()}
    unknown_miles = 0.0
    escalated_miles = 0.0

    for a, b in itertools.pairwise(checkpoints):
        length = max(0.0, b.mile - a.mile)
        level = max(_effective(a.level), _effective(b.level))
        miles[_KEYS[level]] += length
        if a.level is None or b.level is None:
            unknown_miles += length
        else:
            base = max(_effective(a.base_level), _effective(b.base_level))
            if level > base:
                escalated_miles += length

    scores = [int(_effective(cp.level)) for cp in checkpoints]
    avg_risk = sum(scores) / len(scores) if scores else 0.0

    blocked = next((cp for cp in checkpoints if cp.level == Level.NO_TRAVEL), None)
    blocked_at = None
    if blocked is not None:
        blocked_at = {"mile": round(blocked.mile, 1), "drivers": blocked.drivers}

    return RouteSummary(
        route_id=route_id,
        duration_s=duration_s,
        distance_mi=distance_mi,
        miles=miles,
        unknown_miles=unknown_miles,
        load_escalated_miles=escalated_miles,
        avg_risk=avg_risk,
        eligible=blocked is None,
        blocked_at=blocked_at,
    )


def _fmt_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _why_better(win: RouteSummary, other: RouteSummary) -> str:
    """Name the first ranking rule that separates the winner from another route."""
    checks = [
        ("severe", win.miles["severe"], other.miles["severe"], "Fewer Severe miles", lambda v: f"{v:.0f} mi"),
        ("high", win.miles["high"], other.miles["high"], "Fewer High miles", lambda v: f"{v:.0f} mi"),
        ("avg", win.avg_risk, other.avg_risk, "Lower average risk", lambda v: f"{v:.2f}"),
        ("time", win.duration_s, other.duration_s, "Shorter travel time", _fmt_duration),
    ]
    for _, mine, theirs, text, fmt in checks:
        if round(mine, 6) != round(theirs, 6):
            return f"{text} than route {other.route_id} ({fmt(mine)} vs {fmt(theirs)})"
    return f"Same score as route {other.route_id}; kept the first one returned"


def _blocked_reason(summary: RouteSummary) -> str:
    blocked = summary.blocked_at or {}
    drivers = " and ".join(blocked.get("drivers") or ["weather"])
    return f"Route {summary.route_id} excluded: No travel at mile {blocked.get('mile', 0):.0f} ({drivers})"


def recommend(summaries: list[RouteSummary]) -> dict:
    eligible = sorted((s for s in summaries if s.eligible), key=lambda s: s.sort_key())
    blocked = [s for s in summaries if not s.eligible]

    ranking = [s.route_id for s in eligible]
    if not eligible:
        return {
            "status": "no_safe_route",
            "route_id": None,
            "ranking": [],
            "reasons": [_blocked_reason(s) for s in blocked],
        }

    winner = eligible[0]
    reasons = [_why_better(winner, other) for other in eligible[1:]]
    reasons += [_blocked_reason(s) for s in blocked]
    if not reasons:
        reasons = ["Only route available"]
    return {"status": "recommended", "route_id": winner.route_id, "ranking": ranking, "reasons": reasons}
