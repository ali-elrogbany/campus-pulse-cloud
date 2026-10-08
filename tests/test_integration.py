"""End-to-end tests: real handler code against moto, a local simulation of DynamoDB and Cognito.

These exercise the actual DynamoDB queries, key conditions, filters and pagination, which
the faster unit tests deliberately replace with fakes.
"""
import base64
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

import create_user as create_user_script
import get_token as get_token_script
import ingest_handler
import query_handler

TABLE = "test-table"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-3")
    monkeypatch.setenv("TABLE_NAME", TABLE)
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="eu-west-3")
        dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # The handlers cache their table between calls; start every test fresh.
        monkeypatch.setattr(ingest_handler, "_table", None)
        monkeypatch.setattr(query_handler, "_table", None)
        yield dynamodb.Table(TABLE)


def ts(minutes_ago=0, hours_ago=0):
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago, hours=hours_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def send(event_id, event_type="temperature", value=22, severity="normal", building="Lab-B",
         room="B012", timestamp=None):
    body = {
        "event_id": event_id, "building": building, "room": room, "event_type": event_type,
        "value": value, "unit": "x", "severity": severity, "timestamp": timestamp or ts(),
    }
    return ingest_handler.lambda_handler({"body": json.dumps(body)}, None)


def get(route, groups="ops_admin", **params):
    claims = {"email": "tester@example.com"}
    if groups is not None:
        claims["cognito:groups"] = groups
    event = {
        "resource": route,
        "queryStringParameters": {k: str(v) for k, v in params.items()} or None,
        "requestContext": {"authorizer": {"claims": claims}},
    }
    result = query_handler.lambda_handler(event, None)
    return result["statusCode"], json.loads(result["body"])


# ---------------------------------------------------------------- ingest against real DynamoDB


def test_duplicate_event_is_rejected_by_the_real_conditional_write(aws):
    same = ts()
    assert send("evt-dup", timestamp=same)["statusCode"] == 201
    assert send("evt-dup", timestamp=same)["statusCode"] == 409


def test_stored_item_carries_key_and_expiry(aws):
    send("evt-1")
    (stored,) = aws.scan()["Items"]
    assert stored["pk"].startswith("EVENT#")
    assert stored["sk"].endswith("#evt-1")
    assert int(stored["expires_at"]) > 0


# ---------------------------------------------------------------- GET /events


def test_events_returns_newest_first_without_internal_keys(aws):
    send("old", timestamp=ts(minutes_ago=30))
    send("mid", timestamp=ts(minutes_ago=20))
    send("new", timestamp=ts(minutes_ago=10))
    status, body = get("/events", groups="student")
    assert status == 200
    assert [e["event_id"] for e in body["events"]] == ["new", "mid", "old"]
    assert not {"pk", "sk", "expires_at"} & set(body["events"][0])


def test_events_filters_by_building_and_type(aws):
    send("a", building="Lab-B", event_type="energy")
    send("b", building="Library-A", event_type="energy")
    send("c", building="Lab-B", event_type="temperature")
    _, body = get("/events", building="Lab-B", event_type="energy")
    assert [e["event_id"] for e in body["events"]] == ["a"]


# ---------------------------------------------------------------- GET /alerts (ops_admin only)


def test_alerts_returns_only_warning_and_critical(aws):
    send("ok", severity="normal")
    send("warn", severity="warning")
    send("crit", severity="critical")
    status, body = get("/alerts", groups="ops_admin")
    assert status == 200
    assert {a["event_id"] for a in body["alerts"]} == {"warn", "crit"}


def test_alerts_can_be_filtered_by_severity(aws):
    send("warn", severity="warning")
    send("crit", severity="critical")
    _, body = get("/alerts", severity="critical")
    assert [a["event_id"] for a in body["alerts"]] == ["crit"]


def test_alerts_rejects_unknown_severity(aws):
    status, body = get("/alerts", severity="apocalyptic")
    assert status == 400 and body["error"] == "invalid_parameter"


def test_student_gets_403_on_alerts_but_200_on_events_and_stats(aws):
    send("crit", severity="critical")
    assert get("/alerts", groups="student")[0] == 403
    assert get("/events", groups="student")[0] == 200
    assert get("/stats", groups="student")[0] == 200


def test_user_with_no_group_is_forbidden_everywhere(aws):
    for route in ("/events", "/stats", "/alerts"):
        assert get(route, groups=None)[0] == 403


def test_user_in_both_groups_is_treated_as_ops_admin(aws):
    assert get("/alerts", groups="[student ops_admin]")[0] == 200


def test_unknown_route_is_404(aws):
    assert get("/secret")[0] == 404


# ---------------------------------------------------------------- GET /stats


def test_stats_aggregates_the_window(aws):
    send("t1", event_type="temperature", value=20)
    send("t2", event_type="temperature", value=24)
    send("e1", event_type="energy", value=10, building="Library-A")
    send("o1", event_type="occupancy", value=45, severity="critical", room="B104")
    status, body = get("/stats", groups="student")
    assert status == 200
    assert body["total_events"] == 4
    assert body["buildings"]["Lab-B"]["avg_temperature_c"] == 22.0
    assert body["top_energy_building"] == "Library-A"
    assert body["overloaded_rooms"] == ["Lab-B/B104"]
    assert body["truncated"] is False


def test_stats_respects_the_time_window(aws):
    send("recent", timestamp=ts(minutes_ago=5))
    send("stale", timestamp=ts(hours_ago=3))
    assert get("/stats", hours=1)[1]["total_events"] == 1
    assert get("/stats", hours=6)[1]["total_events"] == 2


def test_stats_spans_day_partitions(aws):
    send("today", timestamp=ts(minutes_ago=5))
    send("yesterday", timestamp=ts(hours_ago=30))  # always a different UTC day
    assert get("/stats", hours=48)[1]["total_events"] == 2
    assert get("/stats", hours=24)[1]["total_events"] == 1


def test_stats_follows_dynamodb_pagination(aws, monkeypatch):
    monkeypatch.setattr(query_handler, "PAGE_SIZE", 2)
    for number in range(5):
        send(f"e{number}", timestamp=ts(minutes_ago=number + 1))
    body = get("/stats")[1]
    assert body["total_events"] == 5
    assert body["truncated"] is False


def test_stats_reports_truncation_at_the_item_cap(aws, monkeypatch):
    monkeypatch.setattr(query_handler, "STATS_MAX_ITEMS", 2)
    for number in range(3):
        send(f"e{number}", timestamp=ts(minutes_ago=number + 1))
    body = get("/stats")[1]
    assert body["total_events"] == 2
    assert body["truncated"] is True


@pytest.mark.parametrize("params", [{"hours": 0}, {"hours": 169}, {"hours": "abc"}])
def test_stats_rejects_bad_parameters(aws, params):
    assert get("/stats", **params)[0] == 400


# ---------------------------------------------------------------- the user scripts


def jwt_claims(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_create_user_then_login_returns_a_token(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        idp = boto3.client("cognito-idp", region_name="eu-west-3")
        pool_id = idp.create_user_pool(PoolName="p", UsernameAttributes=["email"])["UserPool"]["Id"]
        for group in ("ops_admin", "student"):
            idp.create_group(UserPoolId=pool_id, GroupName=group)
        client_id = idp.create_user_pool_client(
            UserPoolId=pool_id, ClientName="c", ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH"]
        )["UserPoolClient"]["ClientId"]

        password = "Correct-Horse-9-Battery"
        assert create_user_script.create_user(idp, pool_id, "alice@example.com", "ops_admin", password) is True
        # Running it again updates instead of failing.
        assert create_user_script.create_user(idp, pool_id, "alice@example.com", "ops_admin", password) is False

        groups = idp.admin_list_groups_for_user(UserPoolId=pool_id, Username="alice@example.com")["Groups"]
        assert [g["GroupName"] for g in groups] == ["ops_admin"]

        token = get_token_script.get_id_token(idp, client_id, "alice@example.com", password)
        assert token.count(".") == 2  # a JWT has three dot-separated parts

        with pytest.raises(idp.exceptions.NotAuthorizedException):
            get_token_script.get_id_token(idp, client_id, "alice@example.com", "wrong-password")


# ---------------------------------------------------------------- log markers used by the CloudWatch alarms


def _marker_lines(caplog, marker):
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(marker + " ")]
    assert lines, f"no log line starts with the marker '{marker}'"
    return lines


def test_denied_requests_log_a_standalone_marker_with_json_details(aws, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        get("/alerts", groups="student")
    marker, details = _marker_lines(caplog, "access_denied")[0].split(" ", 1)
    assert marker == "access_denied"
    parsed = json.loads(details)
    assert parsed["route"] == "/alerts" and parsed["groups"] == ["student"]


def test_rejected_and_stored_events_log_their_markers(aws, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        send("good")
        send("bad", event_type="banana")
    assert json.loads(_marker_lines(caplog, "event_stored")[0].split(" ", 1)[1])["event_id"] == "good"
    assert "event_type" in _marker_lines(caplog, "event_rejected")[0]


def test_logs_never_contain_the_request_body_or_tokens(aws, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        send("evt-secret-check", event_type="banana", room="SENSITIVE-ROOM")
    assert "SENSITIVE-ROOM" not in caplog.text
