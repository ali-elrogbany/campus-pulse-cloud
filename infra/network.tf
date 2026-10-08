# =============================================================================
# Private network (slice 3)
#
# A VPC (Virtual Private Cloud) is your own isolated network inside AWS. Our two Lambda
# functions now run INSIDE it, in private subnets in two Availability Zones (separate data
# centres), so the loss of one data centre does not stop the API.
#
# What makes this network "private":
#   - no Internet Gateway: nothing can reach in from the internet, nothing can reach out
#   - no NAT Gateway: it would cost about $33 a month and we do not need it
#   - the ONLY way out is a Gateway VPC Endpoint to DynamoDB, which is free and keeps
#     traffic on the AWS network instead of the public internet
#   - the security group allows no inbound traffic and only HTTPS out to DynamoDB
#
# Note: API Gateway invokes the Lambdas through the Lambda service, not through this
# network, so users still reach the API normally. CloudWatch Logs also still work, because
# the Lambda service ships logs on the function's behalf.
# =============================================================================

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  # Needed so the name dynamodb.<region>.amazonaws.com resolves inside the VPC.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project_name}-vpc" }
}

# Two private subnets, one per Availability Zone: 10.0.1.0/24 and 10.0.2.0/24.
resource "aws_subnet" "private_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false

  tags = { Name = "${var.project_name}-private-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 2)
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = false

  tags = { Name = "${var.project_name}-private-b" }
}

# A route table is the network's map. This one has NO route to the internet; the DynamoDB
# endpoint below adds a single route, to DynamoDB only.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.project_name}-private-rt" }
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoint: a private doorway from the VPC to DynamoDB. Free of charge.
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy            = data.aws_iam_policy_document.dynamodb_endpoint.json

  tags = { Name = "${var.project_name}-dynamodb-endpoint" }
}

# Endpoint policy: a second lock on the doorway. Whatever a Lambda's own IAM role says,
# traffic through this endpoint may only write and query OUR table. Even a compromised or
# over-permissive function could not use this network path to reach any other table.
data "aws_iam_policy_document" "dynamodb_endpoint" {
  statement {
    sid       = "OnlyOurTableOnlyTheNeededActions"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.events.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# Security group = a firewall attached to the Lambdas' network interfaces.
# No ingress rules at all (nothing may connect in). One egress rule: HTTPS to DynamoDB.
resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda-sg"
  description = "CampusPulse Lambdas: no inbound, HTTPS outbound to DynamoDB only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project_name}-lambda-sg" }

  # Lambda releases its network interfaces a few minutes after the functions are deleted,
  # so deleting the group can take a while on "terraform destroy".
  timeouts {
    delete = "30m"
  }
}

resource "aws_vpc_security_group_egress_rule" "lambda_to_dynamodb" {
  security_group_id = aws_security_group.lambda.id
  description       = "HTTPS to DynamoDB through the gateway endpoint"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = aws_vpc_endpoint.dynamodb.prefix_list_id
}
