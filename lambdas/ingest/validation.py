"""Validation of incoming campus events.

Pure Python with no AWS imports, so it can be unit-tested on a laptop.
The rules are documented in docs/contracts.md.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta, timezone

EVENT_TYPES = {
    "occupancy",
    "temperature",
    "humidity",
    "energy",
    "door_status",
    "equipment_failure",
}
SEVERITIES = {"normal", "warning", "critical"}
REQUIRED_FIELDS = ("building", "room", "event_type", "value", "unit", "timestamp")
STRING_FIELDS = ("building", "room", "unit", "device_id")
MAX_STRING_LENGTH = 64
MAX_FUTURE_SKEW = timedelta(minutes=5)
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ValidationError(ValueError):
    """Raised with a list of human-readable problems."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and return it as an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_number(value) -> bool:
    # bool is a subclass of int in Python, so exclude it explicitly.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_event(payload, now: datetime | None = None) -> dict:
    """Validate a decoded JSON payload and return a clean, normalized event.

    Raises ValidationError listing every problem found (not just the first).
    """
    if not isinstance(payload, dict):
        raise ValidationError(["body must be a JSON object"])

    now = now or datetime.now(timezone.utc)
    errors: list[str] = []

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        errors.append("missing required fields: " + ", ".join(missing))

    for field in STRING_FIELDS:
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or not 1 <= len(value) <= MAX_STRING_LENGTH:
                errors.append(f"{field} must be a string of 1-{MAX_STRING_LENGTH} characters")

    if "event_type" in payload and payload["event_type"] not in EVENT_TYPES:
        errors.append("event_type must be one of: " + ", ".join(sorted(EVENT_TYPES)))

    severity = payload.get("severity", "normal")
    if severity not in SEVERITIES:
        errors.append("severity must be one of: " + ", ".join(sorted(SEVERITIES)))

    if "value" in payload:
        value = payload["value"]
        if not _is_number(value) or not math.isfinite(value):
            errors.append("value must be a finite number")

    event_id = payload.get("event_id")
    if event_id is None:
        event_id = "evt-" + uuid.uuid4().hex[:12]
    elif not isinstance(event_id, str) or not ID_PATTERN.match(event_id):
        errors.append("event_id must be 1-64 characters of letters, digits, '_' or '-'")

    timestamp = None
    if "timestamp" in payload:
        try:
            if not isinstance(payload["timestamp"], str):
                raise ValueError
            timestamp = parse_timestamp(payload["timestamp"])
        except ValueError:
            errors.append("timestamp must be an ISO 8601 string, e.g. 2026-10-07T09:30:00Z")
        else:
            if timestamp > now + MAX_FUTURE_SKEW:
                errors.append("timestamp is more than 5 minutes in the future")

    if errors:
        raise ValidationError(errors)

    event = {
        "event_id": event_id,
        "building": payload["building"],
        "room": payload["room"],
        "event_type": payload["event_type"],
        "value": payload["value"],
        "unit": payload["unit"],
        "severity": severity,
        "timestamp": timestamp.strftime(TIMESTAMP_FORMAT),
    }
    if "device_id" in payload:
        event["device_id"] = payload["device_id"]
    return event
