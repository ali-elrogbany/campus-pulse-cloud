# Cost analysis

Report material for the "Cost Analysis" section. The numbers come from `scripts/cost_estimate.py`
(run `python scripts/cost_estimate.py`): every price and assumption is a named constant at the top
of that file, and its arithmetic is unit-tested against hand calculations.

## Read this first: how reliable are these figures?

| Point | Detail |
|---|---|
| Prices | AWS list prices for **US East (N. Virginia)**, read from the public AWS pricing pages on 2026-10-08. Our region is **Europe (Paris)**, where prices are usually somewhat higher. Re-check the figures you quote in the [AWS Pricing Calculator](https://calculator.aws/) for eu-west-3 and say so in the report. |
| Not verified | The CloudWatch dashboard price ($3 beyond the 3 free) and the SNS email allowance could not be read from the pages. We use 1 dashboard (inside the 3 free) and a handful of emails, so neither changes any total. |
| Assumptions | Event size 0.5 KB; each read request consumes about 100 DynamoDB read units; Lambda runs 50 ms (ingest) and 300 ms (reads) at 128 MB; about 600 bytes of logs per request. Replace the Lambda durations with the p50 you measure in the load test and re-run the script. |
| Not included | Data transfer out to the internet, Route 53 / custom domains, KMS, tax, and the (tiny) cost of log storage. |
| Account type | AWS also offers new accounts a time-limited credit/free plan. Check yours in the Billing console; the tables assume you pay list price after the always-free allowances. |
| What is priced | What is **deployed**. DynamoDB point-in-time recovery is deliberately not deployed, so it is not in the totals; its price appears under each table as "not deployed" (see `resilience.md`). |

## The three scenarios

| Scenario | Events / month | Reads / month | Users | How it was chosen |
|---|---:|---:|---:|---|
| Prototype (this project) | 20,000 | 2,000 | 5 | Simulator runs, load tests and the demo |
| Campus | 17,280,000 | 300,000 | 1,000 | 2,000 sensors, one reading every 5 minutes (2,000 x 12 x 24 x 30), 1,000 people opening a dashboard about 10 times a day |
| Large | 172,800,000 | 3,000,000 | 10,000 | The same, 10 times bigger |

"First 12 months" includes API Gateway's 1 million free calls a month, which stop 12 months after account
sign-up. The other allowances used here (Lambda, DynamoDB storage, CloudWatch alarms/logs, Cognito users)
do not expire, so "After 12 months" differs only by the API Gateway line.

### Prototype (this project): 20,000 events and 2,000 reads per month

| Service | Usage | First 12 months | After 12 months |
|---|---|---:|---:|
| API Gateway | 22,000 calls | $0.00 | $0.08 |
| Lambda | 22,000 requests, 200 GB-s | $0.00 | $0.00 |
| DynamoDB (requests + storage) | 20,000 write and 200,000 read units, 0.0 GB | $0.04 | $0.04 |
| CloudWatch (alarms, logs) | 4 alarms, 0.0 GB logs | $0.00 | $0.00 |
| Cognito | 5 active users | $0.00 | $0.00 |
| VPC, gateway endpoint, SNS email | no hourly resources (no NAT) | $0.00 | $0.00 |
| **Total per month** | | **$0.04** | **$0.11** |

Not deployed: point-in-time recovery would add $0.00 a month (0.0 GB x $0.20).

### Campus (2,000 sensors, 1 reading / 5 min): 17,280,000 events and 300,000 reads per month

| Service | Usage | First 12 months | After 12 months |
|---|---|---:|---:|
| API Gateway | 17,580,000 calls | $58.03 | $61.53 |
| Lambda | 17,580,000 requests, 119,250 GB-s | $3.32 | $3.32 |
| DynamoDB (requests + storage) | 17,280,000 write and 30,000,000 read units, 9.9 GB | $14.55 | $14.55 |
| CloudWatch (alarms, logs) | 4 alarms, 9.8 GB logs | $2.41 | $2.41 |
| Cognito | 1,000 active users | $0.00 | $0.00 |
| VPC, gateway endpoint, SNS email | no hourly resources (no NAT) | $0.00 | $0.00 |
| **Total per month** | | **$78.31** | **$81.81** |

Not deployed: point-in-time recovery would add $1.98 a month (9.9 GB x $0.20).

### Large (20,000 sensors, 1 reading / 5 min): 172,800,000 events and 3,000,000 reads per month

| Service | Usage | First 12 months | After 12 months |
|---|---|---:|---:|
| API Gateway | 175,800,000 calls | $611.80 | $615.30 |
| Lambda | 175,800,000 requests, 1,192,500 GB-s | $48.17 | $48.17 |
| DynamoDB (requests + storage) | 172,800,000 write and 300,000,000 read units, 98.9 GB | $163.97 | $163.97 |
| CloudWatch (alarms, logs) | 4 alarms, 98.2 GB logs | $46.62 | $46.62 |
| Cognito | 10,000 active users | $0.00 | $0.00 |
| VPC, gateway endpoint, SNS email | no hourly resources (no NAT) | $0.00 | $0.00 |
| **Total per month** | | **$870.56** | **$874.06** |

Not deployed: point-in-time recovery would add $19.78 a month (98.9 GB x $0.20).

## What the numbers say (the points to make in the report)

1. **The prototype costs about 4 cents a month**, almost all of it DynamoDB requests: on-demand DynamoDB
   has no free request allowance (the free 25 read/write units apply only to provisioned tables), but
   20,000 writes cost about a penny. The AWS Budget alert is the safety net for everything else.
2. **API Gateway is the biggest bill as we scale**: about 74% of the Campus scenario. We pay per call, and
   every sensor reading is one call. Two ways to cut it without redesigning the system: batch several readings into one call
   (the validation schema would accept an array), or use API Gateway's cheaper "HTTP API" type, which has
   no request validation or API keys, so we would lose two security controls (verify its price before quoting it).
3. **Everything else is pay-per-use and scales down to zero.** There is no server to pay for while idle, which is
   the main cost argument for serverless on a campus workload that is quiet at night and in holidays.
4. **Costs we deliberately avoided** (see `networking.md`): a NAT Gateway (about $33 a month, whether used or not),
   interface VPC endpoints (about $7 a month each), a load balancer, EC2 or RDS instances, and a web application firewall.
   The guardrail tests fail if any of these is added by accident.
5. **Point-in-time recovery is the one protection we chose not to pay for.** It would add about $2 a month at Campus scale
   (about 2.5% of the bill) and is the only data-protection feature outside the Free Tier. We leave it off in the prototype and
   recommend it for production; the reasoning is in `resilience.md`.

## Controls that keep the bill from surprising us

| Control | What it limits |
|---|---|
| AWS Budget with email alert (`var.monthly_budget_usd`) | Warns when the month's spend is forecast to pass the limit |
| API usage plan: 10,000 ingest calls a day, 10 per second | Ingest is capped at 300,000 calls a month, which is about $1.05 API Gateway + $0.19 DynamoDB writes, roughly $1.30 at most |
| Stage throttling: 10 requests per second per method, burst 20 | Caps the read routes too, but reads have no daily quota; the budget alert is the backstop there |
| TTL: events expire after 30 days | Storage cannot grow without bound |
| Log retention: 14 days | Logs cannot grow without bound |
| Free-tier guardrail tests | Fail if more than 10 alarms, 3 dashboards or 10 custom metrics, any hourly-billed resource, or point-in-time recovery is added |
| `terraform destroy` after each session | Nothing runs, and nothing is billed, while you are not working |

## How to cross-check against reality

After a few days of use, open **Billing > Cost Explorer** (group by service) and compare with the Prototype
column. Screenshot it for the report: an estimate next to the actual bill is much stronger than an estimate alone.
