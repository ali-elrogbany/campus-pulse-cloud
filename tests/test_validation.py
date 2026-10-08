from datetime import datetime, timezone

import pytest

from validation import ValidationError, validate_event

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def valid_payload(**overrides):
    payload = {
        "building": "Library-A",
        "room": "A203",
        "event_type": "occupancy",
        "value": 42,
        "unit": "people",
        "timestamp": "2026-10-07T09:30:00Z",
    }
    payload.update(overrides)
    return payload


def errors_for(payload):
    with pytest.raises(ValidationError) as excinfo:
        validate_event(payload, now=NOW)
    return excinfo.value.errors


def test_valid_event_gets_defaults():
    event = validate_event(valid_payload(), now=NOW)
    assert event["severity"] == "normal"
    assert event["event_id"].startswith("evt-")
    assert event["timestamp"] == "2026-10-07T09:30:00Z"


def test_brief_appendix_a_example_is_accepted():
    example = {
        "event_id": "evt-2026-0001",
        "building": "Library-A",
        "room": "A203",
        "event_type": "occupancy",
        "value": 42,
        "unit": "people",
        "severity": "normal",
        "timestamp": "2026-05-10T09:30:00Z",
    }
    assert validate_event(example, now=NOW)["event_id"] == "evt-2026-0001"


def test_timestamp_with_offset_is_converted_to_utc():
    event = validate_event(valid_payload(timestamp="2026-10-07T11:30:00+02:00"), now=NOW)
    assert event["timestamp"] == "2026-10-07T09:30:00Z"


def test_unknown_fields_are_dropped():
    event = validate_event(valid_payload(hacker="<script>"), now=NOW)
    assert "hacker" not in event


def test_missing_fields_are_all_reported():
    errors = errors_for({"building": "Library-A"})
    assert any("room" in e and "timestamp" in e for e in errors)


@pytest.mark.parametrize("bad", [None, [], "text", 5])
def test_non_object_body_is_rejected(bad):
    assert errors_for(bad) == ["body must be a JSON object"]


def test_unknown_event_type_is_rejected():
    assert any("event_type" in e for e in errors_for(valid_payload(event_type="banana")))


def test_unknown_severity_is_rejected():
    assert any("severity" in e for e in errors_for(valid_payload(severity="apocalyptic")))


@pytest.mark.parametrize("bad", [True, "42", None, float("nan"), float("inf")])
def test_value_must_be_a_finite_number(bad):
    assert any("value" in e for e in errors_for(valid_payload(value=bad)))


@pytest.mark.parametrize("bad", ["yesterday", "", 12345, "2026-13-45T99:99:99Z"])
def test_bad_timestamp_is_rejected(bad):
    assert any("timestamp" in e for e in errors_for(valid_payload(timestamp=bad)))


def test_timestamp_far_in_future_is_rejected():
    assert any("future" in e for e in errors_for(valid_payload(timestamp="2026-10-07T13:00:00Z")))


def test_timestamp_slightly_in_future_is_tolerated():
    validate_event(valid_payload(timestamp="2026-10-07T12:03:00Z"), now=NOW)


def test_overlong_string_is_rejected():
    assert any("building" in e for e in errors_for(valid_payload(building="x" * 65)))


def test_event_id_with_bad_characters_is_rejected():
    assert any("event_id" in e for e in errors_for(valid_payload(event_id="a b;c")))
