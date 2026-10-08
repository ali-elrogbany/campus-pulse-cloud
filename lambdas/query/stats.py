"""Aggregation of stored events into operational statistics.

Answers the questions from the project brief:
  - Which rooms are currently occupied or overloaded?        -> "rooms", "overloaded_rooms"
  - Which buildings consume the most energy?                  -> "buildings"[...]["energy_kwh"]
  - Are there unusual temperature / humidity / equipment events? -> "by_severity", "buildings"[...]["alerts"]

Pure Python with no AWS imports, so it is unit-tested directly.
"""
from __future__ import annotations

from collections import defaultdict


def _num(value) -> float:
    """DynamoDB hands back Decimal; convert so arithmetic and JSON are simple."""
    return float(value)


def _round(value: float, digits: int):
    return round(value, digits)


def compute_stats(items: list[dict]) -> dict:
    by_type: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    buildings: dict[str, dict] = {}
    latest_occupancy: dict[str, dict] = {}

    # Running sums so averages are computed once at the end.
    temp_sum: dict[str, float] = defaultdict(float)
    temp_n: dict[str, int] = defaultdict(int)
    hum_sum: dict[str, float] = defaultdict(float)
    hum_n: dict[str, int] = defaultdict(int)

    for item in items:
        building = item["building"]
        room_key = f"{building}/{item['room']}"
        event_type = item["event_type"]
        severity = item.get("severity", "normal")
        value = _num(item["value"])

        by_type[event_type] += 1
        by_severity[severity] += 1

        entry = buildings.setdefault(
            building, {"events": 0, "alerts": 0, "energy_kwh": 0.0}
        )
        entry["events"] += 1
        if severity in ("warning", "critical"):
            entry["alerts"] += 1

        if event_type == "energy":
            entry["energy_kwh"] += value
        elif event_type == "temperature":
            temp_sum[building] += value
            temp_n[building] += 1
        elif event_type == "humidity":
            hum_sum[building] += value
            hum_n[building] += 1
        elif event_type == "occupancy":
            # Keep the most recent reading per room, whatever order items arrive in.
            current = latest_occupancy.get(room_key)
            if current is None or item["timestamp"] > current["as_of"]:
                latest_occupancy[room_key] = {
                    "occupancy": value if value != int(value) else int(value),
                    "severity": severity,
                    "as_of": item["timestamp"],
                }

    for building, entry in buildings.items():
        entry["energy_kwh"] = _round(entry["energy_kwh"], 2)
        entry["avg_temperature_c"] = (
            _round(temp_sum[building] / temp_n[building], 1) if temp_n[building] else None
        )
        entry["avg_humidity_pct"] = (
            _round(hum_sum[building] / hum_n[building], 1) if hum_n[building] else None
        )

    rooms = {}
    for room_key in sorted(latest_occupancy):
        reading = dict(latest_occupancy[room_key])
        reading["occupied"] = reading["occupancy"] > 0
        reading["overloaded"] = reading["severity"] == "critical"
        rooms[room_key] = reading

    return {
        "total_events": len(items),
        "by_type": dict(sorted(by_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "buildings": dict(sorted(buildings.items())),
        "rooms": rooms,
        "overloaded_rooms": [key for key, r in rooms.items() if r["overloaded"]],
        "top_energy_building": (
            max(buildings, key=lambda b: buildings[b]["energy_kwh"])
            if any(b["energy_kwh"] > 0 for b in buildings.values())
            else None
        ),
    }
