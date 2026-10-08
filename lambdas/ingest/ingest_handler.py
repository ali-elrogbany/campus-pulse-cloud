"""POST /events - validate one campus event and store it in DynamoDB."""
import json
import logging
import os
import time
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from validation import ValidationError, parse_timestamp, validate_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_table = None


def _get_table():
    """Create the DynamoDB client once per container and reuse it on warm invocations."""
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        return _response(400, {"error": "invalid_json", "message": "Body must be valid JSON"})

    try:
        item = validate_event(payload)
    except ValidationError as exc:
        # Log the reasons only, never the full body.
        logger.warning(json.dumps({"msg": "event_rejected", "errors": exc.errors}))
        return _response(400, {"error": "validation_failed", "details": exc.errors})

    day = parse_timestamp(item["timestamp"]).strftime("%Y-%m-%d")
    ttl_days = int(os.environ.get("EVENT_TTL_DAYS", "30"))

    db_item = dict(item)
    db_item["value"] = Decimal(str(item["value"]))  # DynamoDB does not accept Python floats
    db_item["pk"] = f"EVENT#{day}"
    db_item["sk"] = f"{item['timestamp']}#{item['event_id']}"
    db_item["expires_at"] = int(time.time()) + ttl_days * 86400

    try:
        # The condition makes the write idempotent: a retried event cannot be stored twice.
        _get_table().put_item(Item=db_item, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(409, {"error": "duplicate_event", "event_id": item["event_id"]})
        logger.exception("dynamodb_put_failed")
        return _response(500, {"error": "internal_error"})

    logger.info(
        json.dumps(
            {
                "msg": "event_stored",
                "event_id": item["event_id"],
                "event_type": item["event_type"],
                "severity": item["severity"],
            }
        )
    )
    return _response(201, {"status": "stored", "event_id": item["event_id"]})
