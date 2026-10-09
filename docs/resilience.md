# Resilience, data protection and load testing

Report material for "Scalability", "Reliability / Disaster recovery" and "Testing". Code:
`lambdas/ingest` (idempotent writes), `infra/main.tf` and `infra/network.tf` (permissions, network),
`scripts/load_test.py`.

## Part 1: What protects the data, and from what

| Threat                                                                                       | Protection **deployed** in the prototype                                                                                                                                                                                                                 | Gap                                                   |
| -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| A data centre fails                                                                          | DynamoDB, Lambda, API Gateway and Cognito are multi-AZ managed services; our Lambdas also use two subnets in two zones                                                                                                                                   | A whole-region outage (single region)                 |
| Our own code deletes or overwrites data                                                      | Neither Lambda role has delete or update permission: ingest may only `PutItem`, query may only `Query` (IAM), and the VPC endpoint policy allows only those two actions. Every write also carries a condition that refuses to overwrite an existing item | none for application code                             |
| A person deletes or corrupts data by hand (console, CLI, a bad script run with admin rights) | None. There is **no backup** in the prototype                                                                                                                                                                                                            | Data deleted this way cannot be recovered. See Part 2 |
| The environment is lost or wrecked                                                           | Everything is code: `terraform apply` rebuilds the infrastructure, and the code is in git                                                                                                                                                                | Data is not part of the rebuild                       |
| Duplicate or retried writes                                                                  | Idempotent writes: the same event cannot be stored twice (HTTP 409)                                                                                                                                                                                      | none                                                  |
| A flood of requests                                                                          | Throttling and a daily quota shed the excess with HTTP 429 (Part 3)                                                                                                                                                                                      | none                                                  |
| Data growing forever                                                                         | TTL deletes events after 30 days                                                                                                                                                                                                                         | by design, old events are discarded                   |

Why accepting the gap is reasonable here: the prototype holds only **simulated** events that the simulator can
regenerate in seconds, and events expire after 30 days anyway. Nothing irreplaceable is stored.

## Part 2: Point-in-time recovery, designed but not deployed

**What it is.** DynamoDB keeps a continuous backup of the table. You can create a new table holding the data exactly
as it was at any second in the last 35 days. A restore never overwrites the live table, so trying one is safe.

**Why the prototype does not enable it.** It is not covered by the AWS Free Tier: it is billed per GB of table size
($0.20 per GB-month at US East list prices, to be re-checked for Paris). The prototype is meant to run inside the Free Tier,
and its data is simulated and regenerable, so the benefit does not justify even a small recurring charge.
A guardrail test (`test_point_in_time_recovery_stays_off_to_remain_inside_the_free_tier`) makes sure nobody enables it by accident.

**What it would cost (from `scripts/cost_estimate.py`).**

| Scenario               |    Table size | Added cost per month | Share of the total bill |
| ---------------------- | ------------: | -------------------: | ----------------------: |
| Prototype              | about 0.01 GB |          under $0.01 |              negligible |
| Campus (2,000 sensors) |  about 9.9 GB |          about $1.98 |              about 2.5% |
| Large (20,000 sensors) | about 98.9 GB |         about $19.78 |              about 2.3% |

**Recommendation for a real deployment.** Enable it. It is the cheapest protection against the one threat in Part 1
that nothing else covers, at roughly 2 to 3% of the bill.

**How to enable it** (one block inside `aws_dynamodb_table.events` in `infra/main.tf`, then `terraform apply`; also update the
guardrail test and the cost table):

```hcl
point_in_time_recovery {
  enabled = true
}
```

**Recovery objectives, with and without it**

|                                        | Prototype as deployed                                                                           | With PITR enabled (design)                                                                                    |
| -------------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Recovery point (data you can get back) | Nothing deleted by hand; the infrastructure only                                                | Any second up to about 5 minutes ago, for 35 days (AWS's continuous backup trails real time by a few minutes) |
| Recovery time                          | Time of a `terraform apply` plus regenerating data with the simulator (measure it and quote it) | Restore time grows with table size; it must be measured on the real table, not assumed                        |

**Recovery runbook if it were enabled** (worth including in the report as the operational procedure):

1. Stop the damage and note the time just before it started.
2. Restore the table to that time into a **new** table.
3. Check the data in the new table.
4. Either copy the missing items back into the live table, or point the application at the new table by changing `TABLE_NAME`.
5. Re-apply Terraform: a restore does **not** bring back the TTL setting, alarms and metrics, IAM policies or the PITR setting.

**A test worth doing before trusting it:** a backup that has never been restored is only a hope. With PITR enabled, rehearse a
disaster on marker items in a separate partition (write them, delete them, restore to the moment before, check they are back,
delete the restored table) and record the measured recovery time. This was designed but not run, because the feature is not deployed.

Other options exist and are also billed per GB (on-demand backups, exports to S3). Price them before choosing.

**What to say in the report and the presentation (three sentences).** The architecture includes DynamoDB point-in-time recovery
to protect against human error. The prototype leaves it off because it is the one feature outside the Free Tier and the data is
simulated and regenerable. For production we recommend enabling it, at about $2 a month for a campus of 2,000 sensors.

## Part 3: Load test (`scripts/load_test.py`)

Two phases answer two different questions.

| Phase  | What it sends                                  | Question                                         | Pass condition                              |
| ------ | ---------------------------------------------- | ------------------------------------------------ | ------------------------------------------- |
| Steady | 300 requests at 8 per second (the limit is 10) | Is it fast and error-free when used as designed? | no 5xx, at least 95% stored (HTTP 201)      |
| Burst  | 200 requests, 25 at a time, no pacing          | When overloaded, does it protect itself?         | no 5xx; the excess is refused with HTTP 429 |

How to read the results:

- **p50 / p95 / p99** are the latency that 50% / 95% / 99% of requests beat. The p99 and max include **cold starts**
  (a Lambda container starting for the first time). Say so if the max is large.
- **429 is success of the protection, not failure of the system.** It means API Gateway refused the excess before it reached
  Lambda and DynamoDB, so nothing downstream was overloaded. A 5xx would be a real failure.
- On the dashboard the 429s appear as 4XX errors; the 5XX line and the "5XX alarm" must stay quiet.

**Put your own measured numbers here after the run:**

| Phase  | Requests | Stored (201) | Throttled (429) | 5xx | p50  | p95  | p99  | max  |
| ------ | -------- | ------------ | --------------- | --- | ---- | ---- | ---- | ---- |
| Steady | 300      | 300          | 0               | 0   | 45   | 57   | 70   | 120  |
| Burst  | 200      | 16           | 30              | 154 | 2879 | 3205 | 3205 | 3205 |

After the run, replace the assumed Lambda durations in `scripts/cost_estimate.py` (`ingest_ms`, `query_ms`) with your measured
p50 and re-run it, so the cost table rests on a measurement.

### Scalability argument for the report

- The **API is deliberately limited** (10 requests per second) to protect cost, not because the architecture cannot go further.
  Raise `api_rate_limit` and `api_burst_limit` in `terraform.tfvars` and the same code serves more.
- **Lambda** scales out automatically, one container per concurrent request, up to the account's concurrency limit (check
  yours under Lambda > Dashboard; new accounts can start low).
- **DynamoDB on-demand** adapts to traffic with no capacity to plan. Our partition key is one per day
  (`EVENT#YYYY-MM-DD`), and a single key sustains about 1,000 writes per second. Even the Large scenario in
  `cost-analysis.md` averages only about 67 writes per second (172.8 million a month), so there is roughly 15 times
  headroom. Beyond that, the fix would be to spread each day over several partitions (e.g. `EVENT#2026-10-08#03`)
  and query them all.
- **Cost scales with traffic, not with provisioned capacity**, see `cost-analysis.md`.
