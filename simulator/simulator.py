#!/usr/bin/env python3
"""CampusPulse simulator: generates fake campus sensor events and sends them to the API.

Example:
    export CAMPUSPULSE_URL=https://abc123.execute-api.eu-west-3.amazonaws.com/v1
    export CAMPUSPULSE_API_KEY=...
    python simulator.py --count 60 --rate 2 --anomaly-rate 0.1
"""
import argparse
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests

MAX_RATE = 10.0  # events per second; matches the API usage plan so we never get throttled

# building -> rooms with their capacity (people), plus a typical energy draw in kW
CAMPUS = {
    "Library-A": {"rooms": {"A101": 60, "A203": 45, "A305": 30}, "base_kw": 40},
    "Lab-B": {"rooms": {"B012": 25, "B104": 30}, "base_kw": 90},
    "Engineering-C": {"rooms": {"C201": 120, "C310": 50}, "base_kw": 70},
    "Student-Center-D": {"rooms": {"D001": 200, "D110": 80}, "base_kw": 55},
}

EVENT_TYPES = ["occupancy", "temperature", "humidity", "energy", "door_status", "equipment_failure"]
EVENT_WEIGHTS = [35, 25, 15, 15, 7, 3]  # equipment failures are rare
UNITS = {
    "occupancy": "people",
    "temperature": "celsius",
    "humidity": "percent",
    "energy": "kwh",
    "door_status": "state",
    "equipment_failure": "fault",
}


def _occupancy(capacity, anomaly):
    value = round(capacity * random.uniform(1.1, 1.5)) if anomaly else round(capacity * random.uniform(0, 0.8))
    severity = "critical" if value > capacity else "warning" if value > 0.9 * capacity else "normal"
    return value, severity


def _temperature(anomaly):
    if anomaly:
        value = round(random.choice([random.uniform(32, 42), random.uniform(8, 14)]), 1)
    else:
        value = round(random.gauss(22, 1.5), 1)
    if value > 30 or value < 15:
        return value, "critical"
    if value > 27 or value < 18:
        return value, "warning"
    return value, "normal"


def _humidity(anomaly):
    value = round(random.uniform(80, 95), 1) if anomaly else round(random.uniform(40, 55), 1)
    return value, "critical" if value > 75 else "warning" if value > 65 else "normal"


def _energy(base_kw, anomaly):
    expected = base_kw * 0.25  # kWh used in a 15 minute window
    value = round(expected * (random.uniform(2.5, 4) if anomaly else random.uniform(0.7, 1.3)), 2)
    return value, "critical" if value > 2 * expected else "warning" if value > 1.5 * expected else "normal"


def generate_event(anomaly_rate: float) -> dict:
    building, info = random.choice(list(CAMPUS.items()))
    room, capacity = random.choice(list(info["rooms"].items()))
    event_type = random.choices(EVENT_TYPES, weights=EVENT_WEIGHTS)[0]
    anomaly = random.random() < anomaly_rate

    if event_type == "occupancy":
        value, severity = _occupancy(capacity, anomaly)
    elif event_type == "temperature":
        value, severity = _temperature(anomaly)
    elif event_type == "humidity":
        value, severity = _humidity(anomaly)
    elif event_type == "energy":
        value, severity = _energy(info["base_kw"], anomaly)
    elif event_type == "door_status":
        value, severity = (1, "warning") if anomaly else (random.choice([0, 1]), "normal")
    else:  # equipment_failure
        value, severity = 1, "critical"

    return {
        "building": building,
        "room": room,
        "event_type": event_type,
        "value": value,
        "unit": UNITS[event_type],
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device_id": f"{event_type[:4]}-{room}",
    }


def post_event(session: requests.Session, url: str, event: dict, retries: int = 3) -> int:
    """Send one event. Retries on network errors, 429 and 5xx with exponential backoff."""
    status = 0
    for attempt in range(retries + 1):
        try:
            response = session.post(url, json=event, timeout=10)
            status = response.status_code
            if status != 429 and status < 500:
                return status
        except requests.RequestException:
            status = 0
        time.sleep(min(2**attempt, 8))
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="CampusPulse event simulator")
    parser.add_argument("--url", default=os.environ.get("CAMPUSPULSE_URL"), help="API base URL")
    parser.add_argument("--api-key", default=os.environ.get("CAMPUSPULSE_API_KEY"), help="API key")
    parser.add_argument("--count", type=int, default=60, help="number of events to send (default 60)")
    parser.add_argument("--rate", type=float, default=2.0, help=f"events per second (max {MAX_RATE:g})")
    parser.add_argument("--anomaly-rate", type=float, default=0.1, help="share of abnormal readings, 0-1")
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducible runs")
    args = parser.parse_args()

    if not args.url or not args.api_key:
        parser.error("set --url and --api-key (or CAMPUSPULSE_URL and CAMPUSPULSE_API_KEY)")
    if not 0 < args.rate <= MAX_RATE:
        parser.error(f"--rate must be between 0 and {MAX_RATE:g}")
    if not 0 <= args.anomaly_rate <= 1:
        parser.error("--anomaly-rate must be between 0 and 1")
    if args.count < 1:
        parser.error("--count must be at least 1")

    if args.seed is not None:
        random.seed(args.seed)

    url = args.url.rstrip("/")
    if not url.endswith("/events"):
        url += "/events"

    session = requests.Session()
    session.headers.update({"x-api-key": args.api_key})

    results: Counter = Counter()
    started = time.time()
    try:
        for number in range(1, args.count + 1):
            results[post_event(session, url, generate_event(args.anomaly_rate))] += 1
            if number % 10 == 0:
                print(f"sent {number}/{args.count} ...", flush=True)
            time.sleep(1 / args.rate)
    except KeyboardInterrupt:
        print("\ninterrupted")

    sent = sum(results.values())
    print(f"\nfinished: {sent} events in {time.time() - started:.1f}s")
    for status, count in sorted(results.items()):
        print(f"  HTTP {status or 'network error'}: {count}")
    return 0 if results.get(201, 0) == sent else 1


if __name__ == "__main__":
    sys.exit(main())
