"""The cost table goes into the report, so its arithmetic is checked against hand calculations."""

import pytest

import cost_estimate
from cost_estimate import Scenario, apigw_cost, estimate, pitr_cost, total


def line(lines, service):
    return next(cost for name, _, cost in lines if name == service)


def test_api_gateway_tiers_apply_at_the_boundaries():
    assert apigw_cost(1_000_000) == pytest.approx(3.50)
    assert apigw_cost(333_000_000) == pytest.approx(333 * 3.50)

    # 1 billion calls = 333M at $3.50/M + 667M at $2.80/M
    assert apigw_cost(1_000_000_000) == pytest.approx(
        333 * 3.50 + 667 * 2.80
    )

    # 2 billion calls also use the third tier at $2.38/M
    assert apigw_cost(2_000_000_000) == pytest.approx(
        333 * 3.50 + 667 * 2.80 + 1000 * 2.38
    )


def test_a_tiny_prototype_stays_within_the_free_tier_except_for_dynamodb():
    prototype = Scenario(
        "p",
        events_per_month=20_000,
        reads_per_month=2_000,
        monthly_active_users=5,
    )
    lines = estimate(prototype, first_year=True)

    assert line(lines, "API Gateway") == 0
    assert line(lines, "Lambda") == 0
    assert line(lines, "Cognito") == 0
    assert line(lines, "CloudWatch (alarms, logs)") == 0

    # Writes: 20,000 * $0.7423/M = $0.014846
    # Reads: 2,000 * 100 RRUs * $0.1487/M = $0.029740
    assert line(lines, "DynamoDB (requests + storage)") == pytest.approx(
        0.014846 + 0.029740
    )
    assert total(lines) == pytest.approx(0.044586)


def test_after_the_first_year_only_the_api_gateway_allowance_disappears():
    prototype = Scenario(
        "p",
        events_per_month=20_000,
        reads_per_month=2_000,
        monthly_active_users=5,
    )
    lines = estimate(prototype, first_year=False)

    # 22,000 calls at $3.50/M after the API Gateway allowance expires.
    assert line(lines, "API Gateway") == pytest.approx(
        22_000 / 1e6 * 3.50
    )
    assert line(lines, "Lambda") == 0
    assert line(lines, "CloudWatch (alarms, logs)") == 0
    assert line(lines, "Cognito") == 0

    # During the first year, the 1M-call API Gateway allowance applies.
    assert line(estimate(prototype, first_year=True), "API Gateway") == 0


def test_campus_scale_numbers_match_a_manual_calculation():
    campus = Scenario(
        "c",
        events_per_month=17_280_000,
        reads_per_month=300_000,
        monthly_active_users=1_000,
    )
    lines = estimate(campus, first_year=True)

    # 17.28M events + 300k reads = 17.58M API calls.
    # First-year allowance: 17.58M - 1M = 16.58M billable calls.
    assert line(lines, "API Gateway") == pytest.approx(
        (17_580_000 - 1_000_000) / 1e6 * 3.50
    )

    # Each event is 0.5 KB, rounded up to 1 write unit.
    # Writes: 17.28M * $0.7423/M = $12.826944
    # Reads: 300k * 100 RRUs * $0.1487/M = $4.461
    # Stored data: approximately 9.888 GB, within the 25 GB free allowance.
    assert line(lines, "DynamoDB (requests + storage)") == pytest.approx(
        12.826944 + 4.461
    )

    # 1,000 active users are within Cognito's 10,000-user allowance.
    assert line(lines, "Cognito") == 0


def test_cognito_charges_only_above_the_free_allowance():
    big = Scenario(
        "b",
        events_per_month=0,
        reads_per_month=0,
        monthly_active_users=12_000,
    )

    # 2,000 users above the free allowance at $0.015 per MAU.
    expected = 2_000 * 0.015

    assert line(estimate(big, first_year=True), "Cognito") == pytest.approx(
        expected
    )
    assert line(estimate(big, first_year=False), "Cognito") == pytest.approx(
        expected
    )


def test_the_first_year_is_never_more_expensive_than_later_years():
    for scenario in cost_estimate.SCENARIOS:
        assert total(estimate(scenario, True)) <= total(
            estimate(scenario, False)
        )


def test_costs_grow_with_traffic():
    totals = [
        total(estimate(scenario, True))
        for scenario in cost_estimate.SCENARIOS
    ]

    assert totals == sorted(totals)
    assert len(set(totals)) == 3


# Point-in-time recovery is not deployed, so it must not appear
# in the deployed bill. Its additional cost is shown separately.


def test_the_deployed_bill_contains_no_point_in_time_recovery_line():
    for scenario in cost_estimate.SCENARIOS:
        for service, _, _ in estimate(scenario):
            assert "recovery" not in service.lower()
            assert "pitr" not in service.lower()


def test_the_what_if_price_of_point_in_time_recovery_is_per_gb_of_table_size():
    campus = Scenario(
        "c",
        events_per_month=17_280_000,
        reads_per_month=300_000,
        monthly_active_users=1_000,
    )

    # Stored size:
    # 17.28M * (0.5 KB item + 0.1 KB overhead) / 1,048,576
    # = approximately 9.887695 GB.
    # PITR: 9.887695 GB * $0.23772/GB-month.
    expected = 9.8876953125 * 0.23772

    assert pitr_cost(campus) == pytest.approx(expected, rel=0.001)

    tiny = Scenario(
        "t",
        events_per_month=20_000,
        reads_per_month=0,
        monthly_active_users=1,
    )

    # 20,000 * 0.6 KB / 1,048,576 KB per GB * $0.23772/GB-month
    assert pitr_cost(tiny) == pytest.approx(0.00272049, rel=0.001)
    assert pitr_cost(tiny) < 0.01


def test_the_markdown_renders_every_service_and_the_not_deployed_notice():
    text = cost_estimate.render(markdown=True)

    for service in (
        "API Gateway",
        "Lambda",
        "Cognito",
        "Total per month",
        "Not deployed: point-in-time recovery",
    ):
        assert service in text