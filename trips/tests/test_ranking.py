from trips.ranking import CheckpointRisk, recommend, summarize
from trips.risk import Level

L, M, H, S, N = Level.LOW, Level.MODERATE, Level.HIGH, Level.SEVERE, Level.NO_TRAVEL


def cps(levels, interval=25, base=None):
    base = base or levels
    return [
        CheckpointRisk(mile=i * interval, level=lv, base_level=b)
        for i, (lv, b) in enumerate(zip(levels, base, strict=False))
    ]


def test_worse_endpoint_rule_for_miles():
    # The example agreed in planning: one Severe sample at mile 25 -> 50 Severe miles
    s = summarize("A", cps([L, S, L, L, L]), duration_s=3600, distance_mi=100)
    assert s.miles["severe"] == 50
    assert s.miles["low"] == 50
    assert sum(s.miles.values()) == 100


def test_average_risk_is_plain_mean_of_checkpoints():
    s = summarize("A", cps([L, M, H, S]), duration_s=1, distance_mi=75)
    assert s.avg_risk == (0 + 1 + 2 + 3) / 4


def test_unknown_weather_counts_as_high_and_is_reported():
    s = summarize("A", cps([L, None, L]), duration_s=1, distance_mi=50)
    assert s.miles["high"] == 50
    assert s.unknown_miles == 50
    assert s.avg_risk == 2 / 3


def test_load_escalated_miles():
    s = summarize("A", cps([L, S, L], base=[L, H, L]), duration_s=1, distance_mi=50)
    assert s.load_escalated_miles == 50


def test_no_travel_makes_route_ineligible():
    s = summarize("A", cps([L, N, L]), duration_s=1, distance_mi=50)
    assert not s.eligible
    assert s.blocked_at["mile"] == 25


def test_ranking_order_severe_then_high_then_avg_then_time():
    a = summarize("A", cps([L, S, L]), duration_s=100, distance_mi=50)
    b = summarize("B", cps([L, H, H, L]), duration_s=500, distance_mi=75)
    rec = recommend([a, b])
    assert rec["route_id"] == "B"  # fewer severe miles beats faster
    assert rec["ranking"] == ["B", "A"]
    assert "Fewer Severe miles than route A" in rec["reasons"][0]


def test_high_miles_break_severe_ties():
    a = summarize("A", cps([L, H, L]), duration_s=100, distance_mi=50)
    b = summarize("B", cps([L, M, L]), duration_s=500, distance_mi=50)
    assert recommend([a, b])["route_id"] == "B"


def test_average_risk_breaks_high_ties():
    a = summarize("A", cps([L, M, M, L]), duration_s=100, distance_mi=75)
    b = summarize("B", cps([L, M, L, L]), duration_s=500, distance_mi=75)
    assert recommend([a, b])["route_id"] == "B"


def test_travel_time_decides_when_all_calm():
    a = summarize("A", cps([L, L, L]), duration_s=500, distance_mi=50)
    b = summarize("B", cps([L, L, L]), duration_s=400, distance_mi=60)
    rec = recommend([a, b])
    assert rec["route_id"] == "B"
    assert "Shorter travel time" in rec["reasons"][0]


def test_blocked_route_never_recommended_even_if_otherwise_best():
    a = summarize("A", cps([L, N, L]), duration_s=100, distance_mi=50)
    b = summarize("B", cps([L, S, S, L]), duration_s=900, distance_mi=75)
    rec = recommend([a, b])
    assert rec["route_id"] == "B"
    assert any("Route A excluded" in r for r in rec["reasons"])


def test_no_recommendation_when_every_route_blocked():
    a = summarize("A", cps([L, N]), duration_s=100, distance_mi=25)
    b = summarize("B", cps([N, L]), duration_s=100, distance_mi=25)
    rec = recommend([a, b])
    assert rec["status"] == "no_safe_route"
    assert rec["route_id"] is None
    assert len(rec["reasons"]) == 2
