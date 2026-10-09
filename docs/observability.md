# Observability design

Report material for the "Observability" section. Code: `infra/monitoring.tf` and the `_log()`
helper in both Lambdas.

## The three pillars, as implemented

| Pillar | What we have | Where to see it |
|---|---|---|
| **Logs** | Every Lambda writes one line per important event: `event_stored`, `event_rejected`, `read_request`, `access_denied`, plus crashes with stack traces. 14-day retention. | CloudWatch > Log groups > `/aws/lambda/campuspulse-ingest` and `-query` |
| **Metrics** | AWS built-in (API requests, 4xx/5xx, latency; Lambda invocations, errors, duration; DynamoDB capacity and throttles) plus 2 custom metrics derived from logs | CloudWatch > Dashboards > `campuspulse-operations` |
| **Alarms** | 4 alarms (below) that email the ops team through SNS | CloudWatch > Alarms |

**Log line format.** `marker {json details}`, for example
`access_denied {"route": "/alerts", "user": "student@example.com", "groups": ["student"]}`.
The marker word stands alone so metric filters can count it reliably; the JSON carries details.
Logs never contain passwords, tokens, API keys or full request bodies (a test enforces the last one).

## Alarms

| Alarm | Fires when | Meaning | First thing to check |
|---|---|---|---|
| `campuspulse-api-5xx` | 1+ server errors in 5 min | The API failed a request | Lambda log groups, newest first |
| `campuspulse-ingest-lambda-errors` | 1+ crashes in 5 min | The ingest function threw an unhandled error or timed out | `/aws/lambda/campuspulse-ingest` |
| `campuspulse-query-lambda-errors` | same | Same, read side | `/aws/lambda/campuspulse-query` |
| `campuspulse-access-denied-spike` | 5+ refused requests (403) in 5 min | Someone is probing routes their role cannot use, or a client is misconfigured | Query below: who, which route |

"No data" counts as healthy, so a quiet system does not alarm. Alarms also email when they
return to OK. **Important:** AWS emails a subscription confirmation to the address in
`terraform.tfvars`; until you click the link, no alarm emails are delivered.

## Troubleshooting runbook: "something is wrong, where do I look?"

1. Open the dashboard. Is the problem at the API (red 5xx line), compute (Lambda errors, duration
   spike) or data (DynamoDB throttles)?
2. Open the matching log group and run one of these in **CloudWatch > Logs Insights** (choose the
   log group, then paste):

```
# Why were events rejected? (ingest log group)
fields @timestamp, @message | filter @message like /event_rejected/ | sort @timestamp desc | limit 20

# Who was refused, and on which route? (query log group)
fields @timestamp, @message | filter @message like /access_denied/ | sort @timestamp desc | limit 20

# Crashes with stack traces (either log group)
fields @timestamp, @message | filter @message like /ERROR|Traceback/ | sort @timestamp desc | limit 20

# Is it slow? Duration percentiles per 5 minutes (either log group)
filter @type = "REPORT" | stats avg(@duration), pct(@duration, 95), max(@duration) by bin(5m)

# How often do cold starts happen, and what do they cost? (either log group)
filter @type = "REPORT" and ispresent(@initDuration) | stats count() as coldStarts, avg(@initDuration) as avgInitMs
```

3. Match the symptom to the cause:

| Symptom | Likely cause |
|---|---|
| 5xx and Lambda `Errors` both up | Bug or timeout in a function: read the stack trace |
| 5xx up, Lambda errors flat | API Gateway could not reach or invoke the function (permissions, deployment) |
| Many 4xx, few 5xx | Clients sending bad data (see `event_rejected`) or bad/expired tokens (401) |
| 403 spike | Role misuse or probing (see `access_denied`) |
| Throttled writes in DynamoDB | Load far beyond the prototype; see the scalability section |
| Lambda duration jumps right after a deploy | Cold starts; check `@initDuration` |

## Free-tier accounting (for the Cost Analysis section)

| Item | We use | CloudWatch free tier (verify current terms) |
|---|---|---|
| Alarms | 4 | 10 |
| Dashboards | 1 (about 22 metrics) | 3 dashboards, 50 metrics each |
| Custom metrics (from log filters) | 2 | 10 |
| Log ingestion | well under 1 GB for the project | 5 GB per month |
| SNS email notifications | a handful | 1,000 per month |

## Demonstrating it live (screenshots and demo)

1. Run the simulator to put traffic on the dashboard.
2. Trigger the security alarm on purpose (README step 6): six refused requests in a row.
3. Show: the "access denied" line spiking on the dashboard, the Logs Insights table naming the
   user and route, the alarm turning red, and the email arriving.

## Limitations to admit in the report

- No distributed tracing (X-Ray) and no API Gateway access logs: we can see that requests failed
  and why inside a function, but not a per-request trail through the gateway.
- Alarm evaluation is 5 minutes, so detection takes up to about 5-10 minutes.
- The email address is the only notification channel; a real operation would use a paging tool.
