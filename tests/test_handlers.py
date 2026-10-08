import json
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

import ingest_handler
import query_handler


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "PutItem")


class FakeTable:
    """Stands in for a DynamoDB table: stores items in memory, honours the duplicate check."""

    def __init__(self, fail_with=None):
        self.items = {}
        self.fail_with = fail_with

    def put_item(self, Item, ConditionExpression):
        if self.fail_with:
            raise client_error(self.fail_with)
        key = (Item["pk"], Item["sk"])
        if key in self.items:
            raise client_error("ConditionalCheckFailedException")
        self.items[key] = Item


def post(body):
    return {"body": body if isinstance(body, str) else json.dumps(body)}


def good_event(**overrides):
    event = {
        "event_id": "evt-1",
        "building": "Lab-B",
        "room": "B012",
        "event_type": "temperature",
        "value": 23.5,
        "unit": "celsius",
        "timestamp": "2026-10-07T09:30:00Z",
    }
    event.update(overrides)
    return event


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(ingest_handler, "_get_table", lambda: fake)
    return fake


# ---------------------------------------------------------------- ingest


def test_ingest_stores_valid_event(table):
    result = ingest_handler.lambda_handler(post(good_event()), None)

    assert result["statusCode"] == 201
    assert json.loads(result["body"])["event_id"] == "evt-1"
    (item,) = table.items.values()
    assert item["pk"] == "EVENT#2026-10-07"
    assert item["sk"] == "2026-10-07T09:30:00Z#evt-1"
    assert item["value"] == Decimal("23.5")
    assert item["expires_at"] > 0


def test_ingest_rejects_invalid_json(table):
    result = ingest_handler.lambda_handler(post("{not json"), None)
    assert result["statusCode"] == 400
    assert json.loads(result["body"])["error"] == "invalid_json"
    assert not table.items


def test_ingest_rejects_empty_body(table):
    assert ingest_handler.lambda_handler({"body": None}, None)["statusCode"] == 400


def test_ingest_returns_reasons_for_invalid_event(table):
    result = ingest_handler.lambda_handler(post(good_event(event_type="banana")), None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 400
    assert body["error"] == "validation_failed"
    assert any("event_type" in d for d in body["details"])
    assert not table.items


def test_ingest_duplicate_event_returns_409(table):
    assert ingest_handler.lambda_handler(post(good_event()), None)["statusCode"] == 201
    assert ingest_handler.lambda_handler(post(good_event()), None)["statusCode"] == 409
    assert len(table.items) == 1


def test_ingest_hides_internal_errors(monkeypatch):
    monkeypatch.setattr(ingest_handler, "_get_table", lambda: FakeTable(fail_with="InternalServerError"))
    result = ingest_handler.lambda_handler(post(good_event()), None)
    assert result["statusCode"] == 500
    assert json.loads(result["body"]) == {"error": "internal_error"}


# ---------------------------------------------------------------- query


def make_item(sk, **extra):
    item = {"pk": "EVENT#x", "sk": sk, "expires_at": 1, "building": "Lab-B", "value": Decimal("23.5")}
    item.update(extra)
    return item


@pytest.fixture
def partitions(monkeypatch):
    """Replace the DynamoDB query with canned data; returns the list of requested partitions."""
    requested = []
    data = {}

    def fake_fetch(table, pk, filter_expr, limit):
        requested.append((pk, filter_expr, limit))
        return data.get(pk, [])[:limit]

    monkeypatch.setattr(query_handler, "_get_table", lambda: None)
    monkeypatch.setattr(query_handler, "_fetch_partition", fake_fetch)
    return requested, data


def test_query_strips_internal_keys_and_encodes_decimals(partitions):
    requested, data = partitions
    data[requested_today()] = [make_item("a", value=Decimal("42"))]

    result = query_handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert result["statusCode"] == 200
    assert body["count"] == 1
    assert body["events"][0] == {"building": "Lab-B", "value": 42}


def test_query_spans_days_until_limit_is_reached(partitions):
    requested, data = partitions
    data[requested_today(0)] = [make_item("t1"), make_item("t2")]
    data[requested_today(1)] = [make_item("y1"), make_item("y2")]

    body = json.loads(query_handler.lambda_handler({"queryStringParameters": {"limit": "3"}}, None)["body"])

    assert body["count"] == 3
    assert [pk for pk, _, _ in requested] == [requested_today(0), requested_today(1)]


def test_query_stops_early_when_first_day_is_enough(partitions):
    requested, data = partitions
    data[requested_today(0)] = [make_item("t1"), make_item("t2")]

    query_handler.lambda_handler({"queryStringParameters": {"limit": "2"}}, None)

    assert len(requested) == 1


def test_query_applies_filters(partitions):
    requested, _ = partitions
    query_handler.lambda_handler({"queryStringParameters": {"building": "Lab-B", "event_type": "energy"}}, None)
    assert requested[0][1] is not None


def test_query_without_filters_passes_none(partitions):
    requested, _ = partitions
    query_handler.lambda_handler({}, None)
    assert requested[0][1] is None


@pytest.mark.parametrize(
    "params",
    [{"limit": "0"}, {"limit": "101"}, {"limit": "abc"}, {"days": "0"}, {"days": "8"}],
)
def test_query_rejects_bad_parameters(partitions, params):
    result = query_handler.lambda_handler({"queryStringParameters": params}, None)
    assert result["statusCode"] == 400
    assert json.loads(result["body"])["error"] == "invalid_parameter"


def requested_today(days_ago=0):
    from datetime import datetime, timedelta, timezone

    return f"EVENT#{datetime.now(timezone.utc).date() - timedelta(days=days_ago)}"
