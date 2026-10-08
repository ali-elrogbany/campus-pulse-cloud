"""GET /events - return the latest events, newest first."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_LIMIT, MAX_LIMIT = 20, 100
DEFAULT_DAYS, MAX_DAYS = 2, 7
MAX_PAGES = 10  # safety cap on DynamoDB pages read per day when filters are selective
INTERNAL_KEYS = {"pk", "sk", "expires_at"}

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


class _JsonEncoder(json.JSONEncoder):
    """DynamoDB returns numbers as Decimal, which json cannot encode by default."""

    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=_JsonEncoder),
    }


def _int_param(params: dict, name: str, default: int, low: int, high: int) -> int:
    raw = params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"'{name}' must be an integer") from None
    if not low <= value <= high:
        raise ValueError(f"'{name}' must be between {low} and {high}")
    return value


def _fetch_partition(table, pk: str, filter_expr, limit: int) -> list:
    """Newest-first items of one day partition (a cheap Query, never a Scan)."""
    kwargs = {
        "KeyConditionExpression": Key("pk").eq(pk),
        "ScanIndexForward": False,  # newest first
        "Limit": MAX_LIMIT,
    }
    if filter_expr is not None:
        kwargs["FilterExpression"] = filter_expr

    items: list = []
    for _ in range(MAX_PAGES):
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        last_key = page.get("LastEvaluatedKey")
        if not last_key or len(items) >= limit:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items[:limit]


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    try:
        limit = _int_param(params, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        days = _int_param(params, "days", DEFAULT_DAYS, 1, MAX_DAYS)
    except ValueError as exc:
        return _response(400, {"error": "invalid_parameter", "message": str(exc)})

    filter_expr = None
    if params.get("building"):
        filter_expr = Attr("building").eq(params["building"])
    if params.get("event_type"):
        condition = Attr("event_type").eq(params["event_type"])
        filter_expr = condition if filter_expr is None else filter_expr & condition

    table = _get_table()
    today = datetime.now(timezone.utc).date()
    items: list = []
    for offset in range(days):
        pk = f"EVENT#{today - timedelta(days=offset)}"
        items.extend(_fetch_partition(table, pk, filter_expr, limit - len(items)))
        if len(items) >= limit:
            break

    events = [{k: v for k, v in item.items() if k not in INTERNAL_KEYS} for item in items]
    return _response(200, {"count": len(events), "events": events})
