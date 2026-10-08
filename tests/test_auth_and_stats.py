from decimal import Decimal

import pytest

from auth import OPS_ADMIN, STUDENT, is_allowed, parse_groups
from stats import compute_stats

# ---------------------------------------------------------------- roles


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ops_admin", {"ops_admin"}),
        ("[ops_admin student]", {"ops_admin", "student"}),
        ("[ops_admin, student]", {"ops_admin", "student"}),
        (["student"], {"student"}),
        ("", set()),
        (None, set()),
    ],
)
def test_parse_groups_accepts_every_shape_api_gateway_uses(raw, expected):
    assert parse_groups(raw) == expected


def test_ops_admin_may_use_every_route():
    for route in ("/events", "/stats", "/alerts"):
        assert is_allowed(route, {OPS_ADMIN})


def test_student_may_not_use_alerts():
    assert is_allowed("/events", {STUDENT})
    assert is_allowed("/stats", {STUDENT})
    assert not is_allowed("/alerts", {STUDENT})


def test_user_without_a_group_is_denied_everywhere():
    for route in ("/events", "/stats", "/alerts"):
        assert not is_allowed(route, set())


def test_unknown_group_and_unknown_route_are_denied():
    assert not is_allowed("/events", {"hacker"})
    assert not is_allowed("/admin", {OPS_ADMIN})


# ---------------------------------------------------------------- statistics


def item(building="Lab-B", room="B012", event_type="temperature", value=22, severity="normal",
         timestamp="2026-10-08T09:00:00Z"):
    return {
        "building": building, "room": room, "event_type": event_type,
        "value": Decimal(str(value)), "severity": severity, "timestamp": timestamp,
    }


def test_empty_input_gives_empty_stats():
    result = compute_stats([])
    assert result["total_events"] == 0
    assert result["buildings"] == {}
    assert result["overloaded_rooms"] == []
    assert result["top_energy_building"] is None


def test_counts_by_type_and_severity():
    result = compute_stats([
        item(event_type="temperature"), item(event_type="temperature", severity="warning"),
        item(event_type="energy", value=5, severity="critical"),
    ])
    assert result["by_type"] == {"energy": 1, "temperature": 2}
    assert result["by_severity"] == {"critical": 1, "normal": 1, "warning": 1}


def test_building_averages_and_alert_counts():
    result = compute_stats([
        item(building="Lab-B", event_type="temperature", value=20),
        item(building="Lab-B", event_type="temperature", value=25, severity="warning"),
        item(building="Lab-B", event_type="humidity", value=50),
        item(building="Lab-B", event_type="humidity", value=60),
    ])
    lab = result["buildings"]["Lab-B"]
    assert lab["avg_temperature_c"] == 22.5
    assert lab["avg_humidity_pct"] == 55.0
    assert lab["alerts"] == 1
    assert lab["events"] == 4


def test_building_without_temperature_readings_has_null_average():
    result = compute_stats([item(event_type="energy", value=3)])
    assert result["buildings"]["Lab-B"]["avg_temperature_c"] is None


def test_energy_is_summed_and_the_top_building_is_named():
    result = compute_stats([
        item(building="Lab-B", event_type="energy", value=10.5),
        item(building="Lab-B", event_type="energy", value=4.25),
        item(building="Library-A", event_type="energy", value=3),
    ])
    assert result["buildings"]["Lab-B"]["energy_kwh"] == 14.75
    assert result["top_energy_building"] == "Lab-B"


def test_latest_occupancy_wins_regardless_of_input_order():
    older = item(event_type="occupancy", value=10, timestamp="2026-10-08T08:00:00Z")
    newer = item(event_type="occupancy", value=40, timestamp="2026-10-08T09:00:00Z")
    for ordering in ([older, newer], [newer, older]):
        room = compute_stats(ordering)["rooms"]["Lab-B/B012"]
        assert room["occupancy"] == 40


def test_overloaded_and_empty_rooms_are_flagged():
    result = compute_stats([
        item(room="B012", event_type="occupancy", value=40, severity="critical"),
        item(room="B104", event_type="occupancy", value=0),
        item(room="B105", event_type="occupancy", value=12),
    ])
    assert result["overloaded_rooms"] == ["Lab-B/B012"]
    assert result["rooms"]["Lab-B/B104"]["occupied"] is False
    assert result["rooms"]["Lab-B/B105"]["occupied"] is True
    assert result["rooms"]["Lab-B/B012"]["occupancy"] == 40
    assert isinstance(result["rooms"]["Lab-B/B012"]["occupancy"], int)
