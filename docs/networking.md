# Networking design

Report material for "System Architecture", "Security Design" and "Cost Analysis".
Code: `infra/network.tf`.

## What was built

| Component | Setting | Purpose |
|---|---|---|
| VPC | `10.0.0.0/16`, DNS enabled | Our own isolated network inside AWS |
| Private subnet A | `10.0.1.0/24`, Availability Zone 1 | Home of the Lambda network interfaces |
| Private subnet B | `10.0.2.0/24`, Availability Zone 2 | Same, in a second data centre (high availability) |
| Route table | **no routes** except the DynamoDB endpoint | The network's map: nothing leads to the internet |
| Gateway VPC endpoint (DynamoDB) | free, with an endpoint policy | The only way out, private, never touches the public internet |
| Security group | **0 inbound rules**; 1 outbound rule: TCP 443 to the DynamoDB prefix list | Firewall on the Lambdas |

Deliberately absent: Internet Gateway, NAT Gateway, public subnets, public IPs.

## Traffic map: what can talk to what

```
Internet user / simulator --HTTPS--> API Gateway (managed by AWS, public, protected by
                                      API key or Cognito token, throttled)
API Gateway --Lambda service--> Lambda (inside the VPC)         [not through the VPC network]
Lambda --HTTPS 443--> gateway endpoint --> DynamoDB (our table only)
Lambda --> CloudWatch Logs                                       [shipped by the Lambda service]
Everything else: blocked (no route, no firewall rule)
```

Consequences worth stating in the report:

- **Nothing can connect in.** There is no public address and no inbound rule, so a vulnerable
  library inside a Lambda still cannot be reached from the internet.
- **Nothing can call out except DynamoDB.** Malicious code that ran inside a function could not
  send data to an attacker's server (no route), which limits data exfiltration.
- **Two layers restrict database access.** The Lambda's IAM role allows only `PutItem` (ingest) or
  `Query` (read) on our table, AND the endpoint policy allows only those two actions on our table
  for any traffic through the VPC. Both must allow a call for it to succeed.
- **High availability.** Two Availability Zones: if one data centre fails, Lambda uses the other.
  API Gateway, Lambda, DynamoDB and Cognito are managed multi-AZ services.

## Cost reasoning

| Option | Approx. cost | Decision |
|---|---|---|
| NAT Gateway (lets private subnets reach the internet) | ~$0.045/hour + data, about $33/month | **Avoided**: not needed |
| Interface endpoints (for SNS, CloudWatch API, etc.) | ~$0.01/hour per AZ per endpoint, about $7/month each | **Avoided**: not needed today |
| Gateway endpoint for DynamoDB | free | **Used** |
| VPC, subnets, route tables, security groups | free | Used |
| Lambda network interfaces | no extra charge (shared "Hyperplane" interfaces) | Used |

(Prices are approximate and region-dependent: verify on the AWS pricing pages before quoting
numbers in the report.)

## Trade-offs to discuss honestly

1. **Why put Lambdas in a VPC at all?** Lambda does not require it, and a VPC adds start-up
   complexity and slower teardown. We did it because the project requires a configured network,
   and it gives real security value (egress control, endpoint policy).
2. **The price of isolation.** Any future feature that needs another AWS service from inside the
   VPC (for example publishing to SNS from the anomaly detector) needs either a paid interface
   endpoint or a Lambda kept outside the VPC. We would choose the second option for a low-traffic
   prototype and document it.
3. **No VPC Flow Logs.** They would record every accepted/rejected connection, which is useful
   for forensics, but they add log volume and cost. Listed as a future improvement.
4. **API Gateway is public by design.** It is the one intentional entry point, so it carries the
   protections: HTTPS only, API key, Cognito tokens, request validation, throttling, daily quota.

## Evidence to screenshot

Console: VPC > Your VPCs / Subnets / Route tables / Endpoints / Security groups, and Lambda >
`campuspulse-ingest` > Configuration > VPC. Or from the terminal:

```bash
VPC=$(terraform -chdir=infra output -raw vpc_id)

# Routes: expect only "local" and the DynamoDB prefix list. No 0.0.0.0/0 anywhere.
aws ec2 describe-route-tables --filters Name=vpc-id,Values=$VPC --query 'RouteTables[].Routes'

# Expect an empty list (no internet gateway)
aws ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values=$VPC

# Expect an empty list (no NAT gateway)
aws ec2 describe-nat-gateways --filter Name=vpc-id,Values=$VPC --query 'NatGateways[?State!=`deleted`]'

# Expect "IpPermissions": [] (no inbound rules)
aws ec2 describe-security-groups --filters Name=vpc-id,Values=$VPC Name=group-name,Values=campuspulse-lambda-sg

# Proof the Lambda is inside the VPC, in two subnets
aws lambda get-function-configuration --function-name campuspulse-ingest --query VpcConfig
```
