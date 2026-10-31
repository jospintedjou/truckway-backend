import pytest

from trips.risk import Level, Weather, assess, classify_rain, classify_snow, classify_wind, load_adjusted_wind

L, M, H, S, N = Level.LOW, Level.MODERATE, Level.HIGH, Level.SEVERE, Level.NO_TRAVEL


@pytest.mark.parametrize(
    "mph,expected",
    [(0, L), (24.9, L), (25, M), (34, M), (34.9, M), (35, H), (44.9, H), (45, S), (54.9, S), (55, N), (80, N)],
)
def test_wind_bands(mph, expected):
    assert classify_wind(mph) == expected


@pytest.mark.parametrize(
    "rate,expected",
    [
        (0, L),
        (0.099, L),
        (0.10, M),
        (0.249, M),
        (0.25, H),  # the table lists 0.25 twice; bands include their lower edge
        (0.499, H),
        (0.50, S),
        (1.00, S),  # "No Travel >1.00" so exactly 1.00 is still Severe
        (1.001, N),
    ],
)
def test_rain_bands(rate, expected):
    assert classify_rain(rate) == expected


@pytest.mark.parametrize(
    "rate,expected",
    [(0, L), (0.49, L), (0.5, M), (0.99, M), (1.0, H), (1.99, H), (2.0, S), (3.0, S), (3.01, N)],
)
def test_snow_bands(rate, expected):
    assert classify_snow(rate) == expected


@pytest.mark.parametrize(
    "mph,load,expected",
    [
        (55, 0, N),  # >=55 mph is No Travel for any load
        (50, 30_000, S),  # exactly 30,000 lb: rule needs ">30,000"
        (50, 30_001, N),
        (45, 45_000, N),
        (54.9, 31_000, N),
        (40, 40_000, H),  # exactly 40,000 lb: rule needs ">40,000"
        (40, 40_001, S),
        (35, 80_000, S),
        (34.9, 80_000, M),  # below 35 mph the load rules do not apply
        (20, 80_000, L),
    ],
)
def test_load_rules(mph, load, expected):
    assert load_adjusted_wind(mph, load) == expected


def w(wind=0.0, rain=0.0, snow=0.0):
    return Weather(wind_mph=wind, gust_mph=wind * 1.3, rain_in_hr=rain, snow_in_hr=snow)


def test_assess_takes_worst_condition():
    a = assess(w(wind=26, rain=0.3, snow=0.6), load_lb=10_000)
    assert a.level == H
    assert a.drivers == ["rain"]
    assert not a.load_escalated


def test_assess_reports_ties_as_multiple_drivers():
    a = assess(w(wind=36, rain=0.3), load_lb=10_000)
    assert a.level == H
    assert a.drivers == ["wind", "rain"]


def test_assess_load_escalation_is_flagged():
    a = assess(w(wind=38), load_lb=42_000)
    assert a.level == S
    assert a.base_level == H
    assert a.drivers == ["wind"]
    assert a.load_escalated


def test_assess_escalation_not_flagged_when_other_condition_already_worse():
    a = assess(w(wind=38, snow=2.5), load_lb=42_000)
    assert a.level == S
    assert a.base_level == S
    assert not a.load_escalated
    assert a.drivers == ["wind", "snow"]


def test_calm_weather_has_no_drivers():
    a = assess(w(wind=5), load_lb=45_000)
    assert a.level == L
    assert a.drivers == []
