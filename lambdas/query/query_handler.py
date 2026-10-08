"""Read API: GET /events, GET /stats and GET /alerts.

One function serves all three routes (they only ever read). For every request:
  1. API Gateway has already checked the login token (401 if missing or invalid).
  2. We read the user's role from the verified token and enforce the route's role rule (403).
  3. We run the query and return JSON.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

from auth import get_claims, is_allowed, parse_groups
from stats import compute_stats

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_LIMIT, MAX_LIMIT = 20, 100
DEFAULT_DAYS, MAX_DAYS = 2, 7
MAX_PAGES = 10  # safety cap on DynamoDB pages read per day when filters are selective
INTERNAL_KEYS = {"pk", "sk", "expires_at"}

# /stats and /alerts look back a number of hours rather than whole days.
DEFAULT_HOURS, MAX_HOURS = 24, 168
ALERT_DEFAULT_LIMIT, ALERT_MAX_LIMIT = 50, 200
ALERT_SEVERITIES = ("warning", "critical")
PAGE_SIZE = 500  # items DynamoDB evaluates per page
WINDOW_MAX_PAGES = 20  # hard cap on pages per request: bounds Lambda time and read cost
STATS_MAX_ITEMS = 5000  # hard cap on events aggregated per /stats request

_table = None


def _log(level: int, marker: str, **fields) -> None:
    """Write one log line: a plain marker word, then the details as JSON.

    Example:  access_denied {"route": "/alerts", "user": "bob@example.com", ...}
    The marker stands alone, so CloudWatch metric filters and Logs Insights can find and
    count it reliably (see infra/monitoring.tf). Never log tokens or passwords.
    """
    logger.log(level, "%s %s", marker, json.dumps(fields))


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


def _fetch_window(table, since: datetime, filter_expr, max_items: int):
    """Newest-first items from `since` until now, across day partitions.

    Returns (items, hit_cap). hit_cap is True when we stopped at max_items or at the page cap,
    meaning there may be more matching data than we returned. The sort key starts with an
    ISO timestamp, so "sk >= since" is a cheap range condition, not a scan.
    """
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    day = datetime.now(timezone.utc).date()
    items: list = []
    pages = 0

    while day >= since.date():
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(f"EVENT#{day}") & Key("sk").gte(since_iso),
            "ScanIndexForward": False,
            "Limit": PAGE_SIZE,
        }
        if filter_expr is not None:
            kwargs["FilterExpression"] = filter_expr

        while True:
            if pages >= WINDOW_MAX_PAGES:
                return items, True
            page = table.query(**kwargs)
            pages += 1
            items.extend(page.get("Items", []))
            if len(items) >= max_items:
                return items[:max_items], True
            last_key = page.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        day -= timedelta(days=1)

    return items, False


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in INTERNAL_KEYS}


def handle_stats(params: dict):
    try:
        hours = _int_param(params, "hours", DEFAULT_HOURS, 1, MAX_HOURS)
    except ValueError as exc:
        return _response(400, {"error": "invalid_parameter", "message": str(exc)})

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    items, hit_cap = _fetch_window(_get_table(), since, None, STATS_MAX_ITEMS)
    result = compute_stats(items)
    result["window_hours"] = hours
    result["truncated"] = hit_cap
    result["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _response(200, result)


def handle_alerts(params: dict):
    try:
        limit = _int_param(params, "limit", ALERT_DEFAULT_LIMIT, 1, ALERT_MAX_LIMIT)
        hours = _int_param(params, "hours", DEFAULT_HOURS, 1, MAX_HOURS)
    except ValueError as exc:
        return _response(400, {"error": "invalid_parameter", "message": str(exc)})

    severity = params.get("severity")
    if severity is not None and severity not in ALERT_SEVERITIES:
        return _response(
            400,
            {"error": "invalid_parameter", "message": "'severity' must be 'warning' or 'critical'"},
        )

    wanted = [severity] if severity else list(ALERT_SEVERITIES)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    items, _ = _fetch_window(_get_table(), since, Attr("severity").is_in(wanted), limit)
    alerts = [_clean(item) for item in items]
    return _response(200, {"count": len(alerts), "window_hours": hours, "alerts": alerts})


def handle_events(params: dict):
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

    events = [_clean(item) for item in items]
    return _response(200, {"count": len(events), "events": events})


ROUTES = {"/events": handle_events, "/stats": handle_stats, "/alerts": handle_alerts}


def lambda_handler(event, context):
    route = event.get("resource") or event.get("path") or ""
    handler = ROUTES.get(route)
    if handler is None:
        return _response(404, {"error": "not_found"})

    claims = get_claims(event)
    groups = parse_groups(claims.get("cognito:groups"))
    if not is_allowed(route, groups):
        # Log who was refused (useful evidence for the security section), never the token.
        _log(logging.WARNING, "access_denied", route=route, user=claims.get("email"), groups=sorted(groups))
        return _response(403, {"error": "forbidden", "message": "Your role may not access this resource"})

    _log(logging.INFO, "read_request", route=route, user=claims.get("email"))
    return handler(event.get("queryStringParameters") or {})
