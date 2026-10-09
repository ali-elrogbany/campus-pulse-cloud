#!/usr/bin/env python3
"""Monthly cost estimate for CampusPulse at three sizes, with the arithmetic visible.

Every price below is a constant with its source. Change a constant or a scenario and re-run;
the tables in docs/cost-analysis.md come straight from this script:

    python scripts/cost_estimate.py            # readable tables
    python scripts/cost_estimate.py --markdown # tables to paste into the report

The estimate describes what is DEPLOYED. DynamoDB point-in-time recovery is deliberately not
deployed (it is outside the Free Tier), so it is not in the bill; its price is shown separately
as "what enabling it would add" (see docs/resilience.md).
"""
import argparse
import math
from dataclasses import dataclass

# --------------------------------------------------------------------------- prices (USD)
# Verified on the AWS pricing pages (eu-west-3), 2026-10-08:
DYNAMODB_WRITE_PER_MILLION = 0.7423     # on-demand write request units
DYNAMODB_READ_PER_MILLION = 0.1487      # on-demand read request units
DYNAMODB_STORAGE_PER_GB = 0.29715         # per GB-month, first 25 GB free every month
DYNAMODB_FREE_STORAGE_GB = 25
DYNAMODB_PITR_PER_GB = 0.23772            # continuous backups, per GB-month of table size, no free tier
LAMBDA_PER_MILLION_REQUESTS = 0.20
LAMBDA_PER_GB_SECOND = 0.0000166667    # x86
LAMBDA_FREE_REQUESTS = 1_000_000       # per month, always free
LAMBDA_FREE_GB_SECONDS = 400_000       # per month, always free
APIGW_TIERS = [(333_000_000, 3.50), (667_000_000, 2.80), (math.inf, 2.38)]  # (calls in tier, $ per million)
APIGW_FREE_CALLS = 1_000_000           # per month, but only for the first 12 months after sign-up
CLOUDWATCH_ALARM_EACH = 0.10           # per alarm per month
CLOUDWATCH_FREE_ALARMS = 10
CLOUDWATCH_FREE_DASHBOARDS = 3
CLOUDWATCH_FREE_CUSTOM_METRICS = 10
CLOUDWATCH_CUSTOM_METRIC_EACH = 0.30   # first 10,000 metrics
CLOUDWATCH_LOGS_PER_GB = 0.5985          # ingestion; the pricing page examples disagree (0.25 vs 0.50), so this is the cautious one
CLOUDWATCH_FREE_LOG_GB = 5
COGNITO_FREE_MAU = 10_000              # Lite and Essentials tiers, direct sign-in
COGNITO_PER_MAU = 0.015                # Essentials tier
# NOT verified on the pages I could read (so treated carefully, see docs/cost-analysis.md):
CLOUDWATCH_DASHBOARD_EACH = 3.00       # per dashboard per month beyond the 3 free; we use 1, so it never applies

GB = 1024 ** 3
KB_PER_GB = 1024 ** 2


@dataclass
class Scenario:
    name: str
    events_per_month: int
    reads_per_month: int
    monthly_active_users: int
    item_kb: float = 0.5            # size of one stored event (a few hundred bytes of JSON)
    retention_days: int = 30        # TTL: events older than this are deleted
    rru_per_read: int = 100         # ASSUMPTION: a query/stats read scans a few hundred items
    ingest_ms: int = 45
    query_ms: int = 300             # ASSUMPTION
    lambda_mb: int = 128
    log_bytes_per_invocation: int = 600  # ASSUMPTION: our log line plus Lambda's START/END/REPORT lines
    alarms: int = 4
    dashboards: int = 1
    custom_metrics: int = 2


SCENARIOS = [
    Scenario("Prototype (this project)", events_per_month=20_000, reads_per_month=2_000, monthly_active_users=5),
    Scenario("Campus (2,000 sensors, 1 reading / 5 min)", events_per_month=2_000 * 12 * 24 * 30,
             reads_per_month=300_000, monthly_active_users=1_000),
    Scenario("Large (20,000 sensors, 1 reading / 5 min)", events_per_month=20_000 * 12 * 24 * 30,
             reads_per_month=3_000_000, monthly_active_users=10_000),
]


def apigw_cost(calls: float) -> float:
    cost, remaining = 0.0, calls
    for size, per_million in APIGW_TIERS:
        used = min(remaining, size)
        cost += used / 1_000_000 * per_million
        remaining -= used
        if remaining <= 0:
            break
    return cost


def table_size_gb(s: Scenario) -> float:
    """Stored data once events have been kept for the retention period (+100 bytes overhead per item)."""
    return s.events_per_month * (s.retention_days / 30) * (s.item_kb + 0.1) / KB_PER_GB


def pitr_cost(s: Scenario) -> float:
    """What enabling point-in-time recovery WOULD add per month. Not deployed, not in estimate()."""
    return table_size_gb(s) * DYNAMODB_PITR_PER_GB


def estimate(s: Scenario, first_year: bool = True) -> list:
    """Return [(service, what is billed, monthly cost in USD)] for what is deployed.

    Most allowances (Lambda, DynamoDB storage, CloudWatch, Cognito) never expire. Only API
    Gateway's 1 million free calls stop after the first 12 months of the account, so
    `first_year=False` removes just that one."""
    invocations = s.events_per_month + s.reads_per_month
    stored_gb = table_size_gb(s)

    api_calls = invocations - (APIGW_FREE_CALLS if first_year else 0)
    api = apigw_cost(max(0, api_calls))

    lambda_requests = max(0, invocations - LAMBDA_FREE_REQUESTS)
    gb_seconds = (s.events_per_month * s.ingest_ms + s.reads_per_month * s.query_ms) / 1000 * (s.lambda_mb / 1024)
    lambda_cost = (lambda_requests / 1e6 * LAMBDA_PER_MILLION_REQUESTS
                   + max(0, gb_seconds - LAMBDA_FREE_GB_SECONDS) * LAMBDA_PER_GB_SECOND)

    write_units = s.events_per_month * math.ceil(s.item_kb)          # 1 write unit per KB, rounded up
    read_units = s.reads_per_month * s.rru_per_read
    dynamodb = (write_units / 1e6 * DYNAMODB_WRITE_PER_MILLION
                + read_units / 1e6 * DYNAMODB_READ_PER_MILLION
                + max(0, stored_gb - DYNAMODB_FREE_STORAGE_GB) * DYNAMODB_STORAGE_PER_GB)

    log_gb = invocations * s.log_bytes_per_invocation / GB
    cloudwatch = (max(0, s.alarms - CLOUDWATCH_FREE_ALARMS) * CLOUDWATCH_ALARM_EACH
                  + max(0, s.dashboards - CLOUDWATCH_FREE_DASHBOARDS) * CLOUDWATCH_DASHBOARD_EACH
                  + max(0, s.custom_metrics - CLOUDWATCH_FREE_CUSTOM_METRICS) * CLOUDWATCH_CUSTOM_METRIC_EACH
                  + max(0, log_gb - CLOUDWATCH_FREE_LOG_GB) * CLOUDWATCH_LOGS_PER_GB)

    cognito = max(0, s.monthly_active_users - COGNITO_FREE_MAU) * COGNITO_PER_MAU

    return [
        ("API Gateway", f"{invocations:,.0f} calls", api),
        ("Lambda", f"{invocations:,.0f} requests, {gb_seconds:,.0f} GB-s", lambda_cost),
        ("DynamoDB (requests + storage)", f"{write_units:,.0f} write and {read_units:,.0f} read units, {stored_gb:,.1f} GB", dynamodb),
        ("CloudWatch (alarms, logs)", f"{s.alarms} alarms, {log_gb:,.1f} GB logs", cloudwatch),
        ("Cognito", f"{s.monthly_active_users:,} active users", cognito),
        ("VPC, gateway endpoint, SNS email", "no hourly resources (no NAT)", 0.0),
    ]


def total(lines: list) -> float:
    return sum(cost for _, _, cost in lines)


def render(markdown: bool) -> str:
    out = []
    for scenario in SCENARIOS:
        first_year, later = estimate(scenario, True), estimate(scenario, False)
        out.append(f"\n{'### ' if markdown else ''}{scenario.name}: {scenario.events_per_month:,} events and "
                   f"{scenario.reads_per_month:,} reads per month")
        if markdown:
            out.append("\n| Service | Usage | First 12 months | After 12 months |\n|---|---|---:|---:|")
        for (service, usage, a), (_, _, b) in zip(first_year, later):
            out.append(f"| {service} | {usage} | ${a:,.2f} | ${b:,.2f} |" if markdown
                       else f"  {service:<34} {usage:<58} ${a:>9,.2f} ${b:>9,.2f}")
        out.append(f"| **Total per month** | | **${total(first_year):,.2f}** | **${total(later):,.2f}** |" if markdown
                   else f"  {'TOTAL per month':<34} {'':<58} ${total(first_year):>9,.2f} ${total(later):>9,.2f}")
        extra = pitr_cost(scenario)
        out.append(f"\nNot deployed: point-in-time recovery would add ${extra:,.2f} a month "
                   f"({table_size_gb(scenario):,.1f} GB x ${DYNAMODB_PITR_PER_GB:.2f}).")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="CampusPulse monthly cost estimate")
    parser.add_argument("--markdown", action="store_true", help="print tables for the report")
    print(render(parser.parse_args().markdown))


if __name__ == "__main__":
    main()
