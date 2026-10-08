# CampusPulse 2026

Serverless smart-campus operations platform for the NorthBridge University cloud computing project.
Built on AWS (Lambda, API Gateway, DynamoDB) and deployed with Terraform, inside the Free Tier.

## Repository layout

| Folder | Content |
|---|---|
| `infra/` | Terraform: all AWS resources |
| `lambdas/ingest/` | `POST /events`: validates and stores an event |
| `lambdas/query/` | `GET /events`: returns the latest events |
| `simulator/` | Python script that generates fake campus events |
| `tests/` | Unit tests (run locally, no AWS needed) |
| `docs/` | Contracts (event format, API, table design), report material |

## Prerequisites

- An AWS account, with an **IAM user (not the root user)** that has admin rights and MFA enabled
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), configured with `aws configure`
  (keys stay on your machine. Never commit or share them.)
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.6 or newer
- Python 3.11 or newer

Check that your credentials work: `aws sts get-caller-identity`

## 1. Run the tests (no AWS needed)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

## 2. Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars    # then edit alert_email
terraform init
terraform plan                                  # read it: this is what will be created
terraform apply                                 # type "yes"
```

AWS sends a confirmation email for the budget alert. Screenshot the budget in the console for the report.

## 3. Send events and read them back

```bash
cd ../simulator
pip install -r requirements.txt                 # inside the same virtualenv
export CAMPUSPULSE_URL=$(terraform -chdir=../infra output -raw api_url)
export CAMPUSPULSE_API_KEY=$(terraform -chdir=../infra output -raw api_key)

python simulator.py --count 30 --rate 2
curl "$CAMPUSPULSE_URL/events?limit=5"
curl "$CAMPUSPULSE_URL/events?building=Lab-B&event_type=temperature"
```

Windows PowerShell: use `$env:CAMPUSPULSE_URL = terraform -chdir=../infra output -raw api_url` (same for the key).

### Security and validation checks worth screenshotting

```bash
# No API key -> 403
curl -i -X POST "$CAMPUSPULSE_URL/events" -d '{}'

# Valid key, malformed body -> 400 from API Gateway (the Lambda is never invoked)
curl -i -X POST "$CAMPUSPULSE_URL/events" -H "x-api-key: $CAMPUSPULSE_API_KEY" -d '{"foo": 1}'

# Valid key, wrong event_type -> 400 from the Lambda, with the reason
curl -i -X POST "$CAMPUSPULSE_URL/events" -H "x-api-key: $CAMPUSPULSE_API_KEY" \
  -d '{"building":"Library-A","room":"A203","event_type":"banana","value":1,"unit":"x","timestamp":"2026-10-07T09:30:00Z"}'
```

## 4. Tear down (do this after every work session when you are not demoing)

```bash
cd infra
terraform destroy
```

Screenshot the empty console afterwards: the brief asks for evidence that unused resources were removed.

## Evidence to collect as you go (for the report)

- Budget alert configuration and the confirmation email
- `terraform plan` / `apply` output
- Simulator run and the `GET /events` response
- The 403 and 400 checks above
- CloudWatch log group with the `event_stored` / `event_rejected` log lines
- DynamoDB console showing stored items
- `pytest` passing
