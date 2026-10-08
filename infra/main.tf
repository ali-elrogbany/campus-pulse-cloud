# =============================================================================
# CampusPulse 2026 - slice 1: simulator -> API Gateway -> Lambda -> DynamoDB
# =============================================================================

# -----------------------------------------------------------------------------
# 1. Storage: DynamoDB
#    On-demand billing: no capacity to size, and at prototype scale it costs
#    fractions of a cent. Data is encrypted at rest by default (AWS-owned key).
#    TTL deletes events after var.event_ttl_days, keeping storage tiny.
#    Key design is explained in docs/contracts.md.
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "events" {
  name         = "${var.project_name}-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# -----------------------------------------------------------------------------
# 2. Logs: one log group per Lambda, created by us so we control retention.
#    (If Lambda creates the group itself, logs are kept forever and slowly cost money.)
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.project_name}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "query" {
  name              = "/aws/lambda/${var.project_name}-query"
  retention_in_days = var.log_retention_days
}

# -----------------------------------------------------------------------------
# 3. Identity: one IAM role per Lambda, each with only what it needs.
#    ingest may only PutItem; query may only Query. Neither can delete or scan.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${var.project_name}-ingest-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "query" {
  name               = "${var.project_name}-query-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "WriteEvents"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.events.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

data "aws_iam_policy_document" "query" {
  statement {
    sid       = "ReadEvents"
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.events.arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.query.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest-permissions"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_iam_role_policy" "query" {
  name   = "query-permissions"
  role   = aws_iam_role.query.id
  policy = data.aws_iam_policy_document.query.json
}

# -----------------------------------------------------------------------------
# 4. Compute: two Lambda functions. Terraform zips the source folders itself,
#    so there is no separate build step. boto3 is already in the Lambda runtime.
# -----------------------------------------------------------------------------
data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/ingest"
  output_path = "${path.module}/build/ingest.zip"
  excludes    = ["__pycache__"]
}

data "archive_file" "query" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/query"
  output_path = "${path.module}/build/query.zip"
  excludes    = ["__pycache__"]
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${var.project_name}-ingest"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.12"
  handler          = "ingest_handler.lambda_handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  memory_size      = var.lambda_memory_mb
  timeout          = 10

  environment {
    variables = {
      TABLE_NAME     = aws_dynamodb_table.events.name
      EVENT_TTL_DAYS = tostring(var.event_ttl_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest, aws_iam_role_policy.ingest]
}

resource "aws_lambda_function" "query" {
  function_name    = "${var.project_name}-query"
  role             = aws_iam_role.query.arn
  runtime          = "python3.12"
  handler          = "query_handler.lambda_handler"
  filename         = data.archive_file.query.output_path
  source_code_hash = data.archive_file.query.output_base64sha256
  memory_size      = var.lambda_memory_mb
  timeout          = 10

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.events.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.query, aws_iam_role_policy.query]
}

# -----------------------------------------------------------------------------
# 5. API: API Gateway (REST). Chosen over the cheaper HTTP API because REST API
#    supports API keys + usage plans (needed for the simulator) and request
#    validation at the edge.
# -----------------------------------------------------------------------------
resource "aws_api_gateway_rest_api" "api" {
  name        = "${var.project_name}-api"
  description = "CampusPulse 2026 operations API"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "events" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "events"
}

# First line of input validation: the gateway rejects bodies with missing fields or
# wrong types with a 400 *before* any Lambda runs (so bad requests cost nothing).
# Business rules (enums, timestamp checks) are enforced in the Lambda as well.
resource "aws_api_gateway_model" "event" {
  rest_api_id  = aws_api_gateway_rest_api.api.id
  name         = "Event"
  content_type = "application/json"

  schema = jsonencode({
    "$schema" = "http://json-schema.org/draft-04/schema#"
    title     = "Event"
    type      = "object"
    required  = ["building", "room", "event_type", "value", "unit", "timestamp"]
    properties = {
      event_id   = { type = "string" }
      building   = { type = "string" }
      room       = { type = "string" }
      event_type = { type = "string" }
      value      = { type = "number" }
      unit       = { type = "string" }
      severity   = { type = "string" }
      timestamp  = { type = "string" }
      device_id  = { type = "string" }
    }
  })
}

resource "aws_api_gateway_request_validator" "body" {
  name                        = "validate-body"
  rest_api_id                 = aws_api_gateway_rest_api.api.id
  validate_request_body       = true
  validate_request_parameters = false
}

# POST /events - requires the API key (held by the simulator)
resource "aws_api_gateway_method" "post_events" {
  rest_api_id          = aws_api_gateway_rest_api.api.id
  resource_id          = aws_api_gateway_resource.events.id
  http_method          = "POST"
  authorization        = "NONE"
  api_key_required     = true
  request_validator_id = aws_api_gateway_request_validator.body.id

  request_models = {
    "application/json" = aws_api_gateway_model.event.name
  }
}

resource "aws_api_gateway_integration" "post_events" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.events.id
  http_method             = aws_api_gateway_method.post_events.http_method
  integration_http_method = "POST" # API Gateway always calls Lambda with POST
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.ingest.invoke_arn
}

# GET /events - TEMPORARILY OPEN. Slice 2 replaces this with a Cognito authorizer.
resource "aws_api_gateway_method" "get_events" {
  rest_api_id      = aws_api_gateway_rest_api.api.id
  resource_id      = aws_api_gateway_resource.events.id
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = false
}

resource "aws_api_gateway_integration" "get_events" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.events.id
  http_method             = aws_api_gateway_method.get_events.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query.invoke_arn
}

# Allow API Gateway (and only this API, only this route) to invoke each Lambda.
resource "aws_lambda_permission" "ingest_from_api" {
  statement_id  = "AllowInvokeFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/POST/events"
}

resource "aws_lambda_permission" "query_from_api" {
  statement_id  = "AllowInvokeFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/events"
}

# A deployment is a snapshot of the API; the trigger makes Terraform publish a new
# snapshot whenever any route, integration or the schema changes.
resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.events.id,
      aws_api_gateway_model.event.schema,
      aws_api_gateway_request_validator.body.id,
      aws_api_gateway_method.post_events.id,
      aws_api_gateway_method.post_events.api_key_required,
      aws_api_gateway_integration.post_events.id,
      aws_api_gateway_integration.post_events.uri,
      aws_api_gateway_method.get_events.id,
      aws_api_gateway_method.get_events.authorization,
      aws_api_gateway_integration.get_events.id,
      aws_api_gateway_integration.get_events.uri,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "v1" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = "v1"
}

# Throttling for every method on the stage: protects the backend and the bill.
resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  stage_name  = aws_api_gateway_stage.v1.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = var.api_rate_limit
    throttling_burst_limit = var.api_burst_limit
  }
}

# -----------------------------------------------------------------------------
# 6. Simulator credentials: API key + usage plan (rate limit and daily quota).
#    An API key identifies and throttles a client; it is NOT strong user
#    authentication. Human users will authenticate with Cognito in slice 2.
# -----------------------------------------------------------------------------
resource "aws_api_gateway_api_key" "simulator" {
  name = "${var.project_name}-simulator-key"
}

resource "aws_api_gateway_usage_plan" "simulator" {
  name = "${var.project_name}-simulator-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.api.id
    stage  = aws_api_gateway_stage.v1.stage_name
  }

  throttle_settings {
    rate_limit  = var.api_rate_limit
    burst_limit = var.api_burst_limit
  }

  quota_settings {
    limit  = var.api_daily_quota
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "simulator" {
  key_id        = aws_api_gateway_api_key.simulator.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.simulator.id
}

# -----------------------------------------------------------------------------
# 7. Cost control: AWS Budget with email alerts (required by the project brief).
# -----------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  count = var.create_budget ? 1 : 0

  name         = "${var.project_name}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
