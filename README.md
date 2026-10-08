# CampusPulse 2026

Serverless smart-campus operations platform for the NorthBridge University cloud computing project.
Built on AWS (Lambda, API Gateway, DynamoDB, Cognito) and deployed with Terraform, inside the Free Tier.

## Repository layout

| Folder | Content |
|---|---|
| `infra/` | Terraform: all AWS resources (`main.tf` core, `auth.tf` login/roles, `routes_read.tf` read routes, `network.tf` private network, `monitoring.tf` dashboard and alarms) |
| `lambdas/ingest/` | `POST /events`: validates and stores an event |
| `lambdas/query/` | `GET /events`, `/stats`, `/alerts`: role check, then query |
| `simulator/` | Python script that generates fake campus events |
| `scripts/` | `create_user.py` and `get_token.py`: manage and log in users |
| `tests/` | Automated tests (run locally, no AWS account needed) |
| `docs/` | Contracts (event format, API, roles, table design), `networking.md`, `observability.md`, report material |

## Prerequisites

- An AWS account, with an **IAM user (not the root user)** that has admin rights and MFA enabled
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), configured with `aws configure`
  (keys stay on your machine. Never commit or share them.)
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.6 or newer
- Python 3.11 or newer

Check that your credentials work: `aws sts get-caller-identity`

## 1. Run the tests (no AWS needed)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

The tests include "guardrails" that read the Terraform files and fail if someone adds a paid resource,
an open firewall rule or an unprotected route.

## 2. Deploy (or update)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars    # first time only: then edit alert_email
terraform init
terraform plan                                  # read it: + create, ~ change, - destroy
terraform apply                                 # type "yes"
```

AWS sends two emails: the budget alert confirmation, and an **SNS subscription confirmation** for the alarms.
**Click "Confirm subscription" in the SNS email.** Until you do, no alarm email can reach you.

Slice 3 puts the Lambdas inside a VPC. Expect `terraform plan` to show about 20 new resources
(network, alarms, dashboard) and the two Lambdas updated in place. The first apply can take a few minutes.

## 3. Create users (one admin, one student)

Accounts are created by an administrator only. The script asks for the password at a hidden prompt.

```bash
# from the repository root, with the virtualenv active
export AWS_DEFAULT_REGION=eu-west-3
export CAMPUSPULSE_USER_POOL_ID=$(terraform -chdir=infra output -raw user_pool_id)

python scripts/create_user.py admin@example.com ops_admin
python scripts/create_user.py student@example.com student
```

Passwords need at least 12 characters with upper case, lower case, a digit and a symbol.
Emails do not have to be real (nothing is sent), but use something you will recognise.

## 4. Log in and call the API

```bash
export CAMPUSPULSE_URL=$(terraform -chdir=infra output -raw api_url)
export CAMPUSPULSE_API_KEY=$(terraform -chdir=infra output -raw api_key)
export CAMPUSPULSE_CLIENT_ID=$(terraform -chdir=infra output -raw user_pool_client_id)

# 1. Put some data in (the simulator uses the API key, not a user login)
pip install -r simulator/requirements.txt
python simulator/simulator.py --count 60 --rate 2 --anomaly-rate 0.2

# 2. Log in as each role. The token lasts 60 minutes, then log in again.
ADMIN_TOKEN=$(python scripts/get_token.py admin@example.com)
STUDENT_TOKEN=$(python scripts/get_token.py student@example.com)

# 3. Read the data
curl -s -H "Authorization: $ADMIN_TOKEN" "$CAMPUSPULSE_URL/events?limit=5"
curl -s -H "Authorization: $ADMIN_TOKEN" "$CAMPUSPULSE_URL/stats"
curl -s -H "Authorization: $ADMIN_TOKEN" "$CAMPUSPULSE_URL/alerts?severity=critical"
```

Tip: pipe any response through `python3 -m json.tool` to pretty-print it.

### The security demo (screenshot every line of this)

```bash
# No token at all -> 401 from API Gateway (our code never ran)
curl -i "$CAMPUSPULSE_URL/events"

# Garbage token -> 401
curl -i -H "Authorization: not-a-real-token" "$CAMPUSPULSE_URL/events"

# Student can read events and stats -> 200
curl -i -H "Authorization: $STUDENT_TOKEN" "$CAMPUSPULSE_URL/stats"

# Student on the admin-only route -> 403 (the role check, the centrepiece of the demo)
curl -i -H "Authorization: $STUDENT_TOKEN" "$CAMPUSPULSE_URL/alerts"

# Admin on the same route -> 200
curl -i -H "Authorization: $ADMIN_TOKEN" "$CAMPUSPULSE_URL/alerts"

# Ingest without the API key -> 403
curl -i -X POST "$CAMPUSPULSE_URL/events" -d '{}'

# Valid key, malformed body -> 400 from API Gateway (the Lambda is never invoked)
curl -i -X POST "$CAMPUSPULSE_URL/events" -H "x-api-key: $CAMPUSPULSE_API_KEY" -d '{"foo": 1}'

# Valid key, wrong event_type -> 400 from the Lambda, with the reason
curl -i -X POST "$CAMPUSPULSE_URL/events" -H "x-api-key: $CAMPUSPULSE_API_KEY" \
  -d '{"building":"Library-A","room":"A203","event_type":"banana","value":1,"unit":"x","timestamp":"2026-10-07T09:30:00Z"}'
```

Windows PowerShell: use `$env:NAME = ...` instead of `export NAME=...`, and `curl.exe` instead of `curl`.

## 5. Check the network and the dashboard

```bash
terraform -chdir=infra output dashboard_url      # open this in the browser, then run the simulator
VPC=$(terraform -chdir=infra output -raw vpc_id)

aws ec2 describe-route-tables --filters Name=vpc-id,Values=$VPC --query 'RouteTables[].Routes'   # no 0.0.0.0/0
aws ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values=$VPC                   # empty
aws ec2 describe-nat-gateways --filter Name=vpc-id,Values=$VPC --query 'NatGateways[?State!=`deleted`]'  # empty
aws lambda get-function-configuration --function-name campuspulse-ingest --query VpcConfig        # two subnets
```

Details and screenshots to take: `docs/networking.md`. Dashboard, alarms and Logs Insights queries: `docs/observability.md`.

## 6. Trigger an alarm on purpose

Six refused requests in a row (a student calling the admin-only route) trip the "access denied spike" alarm.

```bash
for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: $STUDENT_TOKEN" "$CAMPUSPULSE_URL/alerts"; done
```

Within roughly 5 to 10 minutes: CloudWatch > Alarms shows `campuspulse-access-denied-spike` in red,
and an email arrives (if you confirmed the SNS subscription). Screenshot all three. It turns green again by itself.

## 7. Tear down (do this after every work session when you are not demoing)

```bash
cd infra
terraform destroy
```

Screenshot the empty console afterwards: the brief asks for evidence that unused resources were removed.
After a destroy, the Cognito users are gone too: run step 3 again after the next deploy.
The destroy can take several extra minutes: Lambda releases its network interfaces slowly. Be patient, do not interrupt it.

## Evidence to collect as you go (for the report)

- Budget alert configuration and the confirmation email
- `terraform plan` / `apply` output
- Simulator run and the `GET /events` response
- The full security demo above (401, 403, 200, 400)
- Cognito user pool and its two groups in the console
- CloudWatch log lines: `event_stored`, `event_rejected`, `access_denied`
- DynamoDB console showing stored items
- CloudWatch dashboard `campuspulse-operations` with live traffic
- The four alarms, SNS confirmation email, and the alarm email from step 6
- VPC, subnets, route table, endpoint and security group in the console (or the CLI output from step 5)
- Logs Insights results (see `docs/observability.md`)
- `pytest` passing
