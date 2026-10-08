import json
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

import ingest_handler


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
