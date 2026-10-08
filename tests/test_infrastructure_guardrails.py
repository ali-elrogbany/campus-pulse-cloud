"""Guardrails: automatic checks on the Terraform files.

These do not deploy anything. They read infra/*.tf and fail if someone adds something that
breaks a cost or security rule we promised in the project report. If a test here fails, the
change is probably a mistake; if it is intentional, update the rule AND the report.
"""
import glob
from pathlib import Path

import hcl2
import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra"


def unquote(text):
    return text.strip('"')


def load_resources():
    """Return {(type, name): attributes} for every resource in infra/*.tf."""
    resources = {}
    for path in sorted(glob.glob(str(INFRA / "*.tf"))):
        with open(path) as handle:
            data = hcl2.load(handle)
        for block in data.get("resource", []):
            for rtype, named in block.items():
                for name, attrs in named.items():
                    resources[(unquote(rtype), unquote(name))] = attrs
    return resources


@pytest.fixture(scope="module")
def resources():
    return load_resources()


def of_type(resources, rtype):
    return {name: attrs for (t, name), attrs in resources.items() if t == rtype}


# ---------------------------------------------------------------- cost guardrails

# Resources that bill by the hour (or are easy to leave running). None may appear.
PAID_OR_RISKY = [
    "aws_nat_gateway", "aws_eip", "aws_instance", "aws_db_instance", "aws_rds_cluster",
    "aws_lb", "aws_alb", "aws_elasticache_cluster", "aws_wafv2_web_acl", "aws_kms_key",
    "aws_vpc_endpoint_service", "aws_cloudfront_distribution", "aws_elasticsearch_domain",
    "aws_opensearch_domain", "aws_eks_cluster", "aws_ecs_cluster", "aws_mq_broker",
]


@pytest.mark.parametrize("rtype", PAID_OR_RISKY)
def test_no_hourly_billed_resources(resources, rtype):
    assert not of_type(resources, rtype), f"{rtype} costs money; justify it in the cost analysis first"


def test_every_log_group_has_a_retention_period(resources):
    groups = of_type(resources, "aws_cloudwatch_log_group")
    assert groups, "expected log groups"
    for name, attrs in groups.items():
        assert "retention_in_days" in attrs, f"log group {name} would keep logs (and cost) forever"


def test_dynamodb_is_on_demand_with_expiry(resources):
    (table,) = of_type(resources, "aws_dynamodb_table").values()
    assert unquote(table["billing_mode"]) == "PAY_PER_REQUEST"
    assert "ttl" in table


def test_api_is_throttled_and_the_simulator_has_a_daily_quota(resources):
    assert of_type(resources, "aws_api_gateway_method_settings")
    assert "quota_settings" in next(iter(of_type(resources, "aws_api_gateway_usage_plan").values()))


# ---------------------------------------------------------------- network guardrails


def test_the_network_has_no_door_to_the_internet(resources):
    assert not of_type(resources, "aws_internet_gateway")
    assert not of_type(resources, "aws_route"), "a standalone route could open a path out"
    for name, attrs in of_type(resources, "aws_route_table").items():
        assert "route" not in attrs, f"route table {name} must not define routes; endpoints add their own"


def test_subnets_are_private_and_in_two_availability_zones(resources):
    subnets = of_type(resources, "aws_subnet")
    assert len(subnets) == 2
    zones = {attrs["availability_zone"] for attrs in subnets.values()}
    assert len(zones) == 2, "subnets must be in different Availability Zones"
    for name, attrs in subnets.items():
        assert attrs.get("map_public_ip_on_launch") is False, f"subnet {name} must not hand out public IPs"


def test_lambda_security_group_allows_nothing_inbound(resources):
    for name, attrs in of_type(resources, "aws_security_group").items():
        assert "ingress" not in attrs, f"security group {name} must have no inbound rules"
    assert not of_type(resources, "aws_vpc_security_group_ingress_rule")
    assert not of_type(resources, "aws_security_group_rule")


def test_outbound_traffic_is_limited_to_https_to_the_endpoint_prefix_list(resources):
    rules = of_type(resources, "aws_vpc_security_group_egress_rule")
    assert rules, "the Lambdas need exactly one way out"
    for name, attrs in rules.items():
        assert "cidr_ipv4" not in attrs and "cidr_ipv6" not in attrs, f"{name}: no open CIDR ranges"
        assert "prefix_list_id" in attrs
        assert attrs["from_port"] == attrs["to_port"] == 443


def test_every_lambda_runs_inside_the_vpc_in_two_subnets(resources):
    functions = of_type(resources, "aws_lambda_function")
    assert len(functions) >= 2
    for name, attrs in functions.items():
        assert "vpc_config" in attrs, f"Lambda {name} must run inside the VPC"


def test_dynamodb_endpoint_is_a_gateway_endpoint_with_a_policy(resources):
    (endpoint,) = of_type(resources, "aws_vpc_endpoint").values()
    assert unquote(endpoint["vpc_endpoint_type"]) == "Gateway"  # free, unlike interface endpoints
    assert "policy" in endpoint


# ---------------------------------------------------------------- security guardrails


def test_no_hardcoded_secrets_or_open_cidrs_anywhere():
    for path in glob.glob(str(INFRA / "*.tf")):
        text = Path(path).read_text()
        assert "0.0.0.0/0" not in text, f"{path}: open-to-the-world CIDR"
        assert "AKIA" not in text, f"{path}: looks like an AWS access key"
        assert "secret_key" not in text.lower(), f"{path}: secrets must never be in Terraform"


def test_signup_is_disabled_and_every_read_route_requires_a_token(resources):
    (pool,) = of_type(resources, "aws_cognito_user_pool").values()
    config = pool["admin_create_user_config"][0]
    assert config["allow_admin_create_user_only"] is True

    for name in ("get_events", "get_stats", "get_alerts"):
        method = resources[("aws_api_gateway_method", name)]
        assert unquote(method["authorization"]) == "COGNITO_USER_POOLS", f"{name} is not protected"


# ---------------------------------------------------------------- monitoring guardrails


def test_free_tier_limits_for_monitoring_are_respected(resources):
    alarms = of_type(resources, "aws_cloudwatch_metric_alarm")
    # for_each alarms count once here but create one alarm per function (2), so budget for that.
    assert len(alarms) + 1 <= 10, "CloudWatch free tier covers 10 alarms"
    assert len(of_type(resources, "aws_cloudwatch_dashboard")) <= 3, "free tier covers 3 dashboards"
    assert len(of_type(resources, "aws_cloudwatch_log_metric_filter")) <= 10, "free tier covers 10 custom metrics"


def test_alarms_notify_someone(resources):
    for name, attrs in of_type(resources, "aws_cloudwatch_metric_alarm").items():
        assert "alarm_actions" in attrs, f"alarm {name} would fire silently"
