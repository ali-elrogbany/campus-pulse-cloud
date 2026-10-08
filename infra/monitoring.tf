# =============================================================================
# Monitoring and alerting (slice 3)
#
# Three layers, from "what is happening" to "tell me when it breaks":
#   1. Dashboard      one screen with traffic, latency, errors, load and security signals
#   2. Metric filters turn specific log lines (access_denied, event_rejected) into numbers
#   3. Alarms         watch the key numbers and email the ops team when they go wrong
#
# Everything here fits the CloudWatch free tier: the free tier includes 10 alarms,
# 3 dashboards of up to 50 metrics, 10 custom metrics and 5 GB of log ingestion per month.
# We use 4 alarms, 1 dashboard (about 22 metrics) and 2 custom metrics.
# =============================================================================

locals {
  api_name  = aws_api_gateway_rest_api.api.name
  api_stage = aws_api_gateway_stage.v1.stage_name

  lambda_functions = {
    ingest = aws_lambda_function.ingest.function_name
    query  = aws_lambda_function.query.function_name
  }
}

# -----------------------------------------------------------------------------
# Notifications: alarms publish to this SNS topic, which emails the ops team.
# AWS sends a confirmation email to var.alert_email: the link in it MUST be clicked,
# otherwise no alarm emails are delivered.
# -----------------------------------------------------------------------------
resource "aws_sns_topic" "alarms" {
  name = "${var.project_name}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# -----------------------------------------------------------------------------
# Metric filters: count log lines that carry a marker word (see _log() in the Lambdas).
# The numbers appear under the "CampusPulse" namespace in CloudWatch metrics.
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "rejected_events" {
  name           = "${var.project_name}-rejected-events"
  log_group_name = aws_cloudwatch_log_group.ingest.name
  pattern        = "\"event_rejected\""

  metric_transformation {
    name      = "RejectedEvents"
    namespace = "CampusPulse"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "access_denied" {
  name           = "${var.project_name}-access-denied"
  log_group_name = aws_cloudwatch_log_group.query.name
  pattern        = "\"access_denied\""

  metric_transformation {
    name      = "AccessDenied"
    namespace = "CampusPulse"
    value     = "1"
    unit      = "Count"
  }
}

# -----------------------------------------------------------------------------
# Alarms. Each looks at 5-minute totals. "No data" counts as healthy (nothing happened).
# -----------------------------------------------------------------------------

# Any server-side error returned by the API (HTTP 5xx).
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.project_name}-api-5xx"
  alarm_description   = "The API returned a server error (HTTP 5xx). Check the Lambda log groups."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  dimensions          = { ApiName = local.api_name, Stage = local.api_stage }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# A Lambda function crashed (unhandled exception, timeout or out-of-memory). One alarm each.
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = local.lambda_functions

  alarm_name          = "${var.project_name}-${each.key}-lambda-errors"
  alarm_description   = "The ${each.key} Lambda function failed. Read its log group in CloudWatch."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# Security signal: five or more refused requests in five minutes means someone is probing
# routes their role may not use (or a script is misconfigured).
resource "aws_cloudwatch_metric_alarm" "access_denied_spike" {
  alarm_name          = "${var.project_name}-access-denied-spike"
  alarm_description   = "5+ requests were refused (HTTP 403) in 5 minutes. Query the access_denied log lines to see who."
  namespace           = "CampusPulse"
  metric_name         = "AccessDenied"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  # The metric filters must exist before the alarm can find the metric.
  depends_on = [aws_cloudwatch_log_metric_filter.access_denied]
}

# -----------------------------------------------------------------------------
# Dashboard: one screen to answer "is the platform healthy right now?"
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project_name}-operations"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 2
        properties = {
          markdown = "# CampusPulse operations\nTraffic, latency and errors at the top; Lambda and DynamoDB health in the middle; security signals and live log queries at the bottom. Alarms email the ops team."
        }
      },

      # ---- Row 1: the API as users see it
      {
        type = "metric", x = 0, y = 2, width = 12, height = 6
        properties = {
          title  = "API requests and errors (5 min totals)"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiName", local.api_name, "Stage", local.api_stage, { label = "Requests" }],
            ["AWS/ApiGateway", "4XXError", "ApiName", local.api_name, "Stage", local.api_stage, { label = "4xx client errors" }],
            ["AWS/ApiGateway", "5XXError", "ApiName", local.api_name, "Stage", local.api_stage, { label = "5xx server errors" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 2, width = 12, height = 6
        properties = {
          title  = "API latency in milliseconds"
          region = var.aws_region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/ApiGateway", "Latency", "ApiName", local.api_name, "Stage", local.api_stage, { stat = "p50", label = "median (p50)" }],
            ["AWS/ApiGateway", "Latency", "ApiName", local.api_name, "Stage", local.api_stage, { stat = "p90", label = "p90" }],
            ["AWS/ApiGateway", "Latency", "ApiName", local.api_name, "Stage", local.api_stage, { stat = "p99", label = "p99" }],
          ]
        }
      },

      # ---- Row 2: the compute layer
      {
        type = "metric", x = 0, y = 8, width = 12, height = 6
        properties = {
          title  = "Lambda invocations, errors and throttles"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", local.lambda_functions.ingest, { label = "ingest calls" }],
            ["AWS/Lambda", "Invocations", "FunctionName", local.lambda_functions.query, { label = "query calls" }],
            ["AWS/Lambda", "Errors", "FunctionName", local.lambda_functions.ingest, { label = "ingest errors" }],
            ["AWS/Lambda", "Errors", "FunctionName", local.lambda_functions.query, { label = "query errors" }],
            ["AWS/Lambda", "Throttles", "FunctionName", local.lambda_functions.ingest, { label = "ingest throttles" }],
            ["AWS/Lambda", "Throttles", "FunctionName", local.lambda_functions.query, { label = "query throttles" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 8, width = 12, height = 6
        properties = {
          title  = "Lambda duration in milliseconds"
          region = var.aws_region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", local.lambda_functions.ingest, { stat = "p50", label = "ingest median" }],
            ["AWS/Lambda", "Duration", "FunctionName", local.lambda_functions.ingest, { stat = "p99", label = "ingest p99" }],
            ["AWS/Lambda", "Duration", "FunctionName", local.lambda_functions.query, { stat = "p50", label = "query median" }],
            ["AWS/Lambda", "Duration", "FunctionName", local.lambda_functions.query, { stat = "p99", label = "query p99" }],
          ]
        }
      },

      # ---- Row 3: the data layer and the security signals
      {
        type = "metric", x = 0, y = 14, width = 12, height = 6
        properties = {
          title  = "DynamoDB load"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.events.name, { label = "write units used" }],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.events.name, { label = "read units used" }],
            ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", aws_dynamodb_table.events.name, { label = "write throttles" }],
            ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", aws_dynamodb_table.events.name, { label = "read throttles" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 14, width = 12, height = 6
        properties = {
          title  = "Security and data-quality signals (5 min totals)"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["CampusPulse", "AccessDenied", { label = "access denied (403)" }],
            ["CampusPulse", "RejectedEvents", { label = "rejected events (bad data)" }],
          ]
        }
      },

      # ---- Row 4: live log queries (CloudWatch Logs Insights)
      {
        type = "log", x = 0, y = 20, width = 12, height = 6
        properties = {
          title  = "Events stored vs rejected"
          region = var.aws_region
          view   = "timeSeries"
          query  = "SOURCE '${aws_cloudwatch_log_group.ingest.name}' | filter @message like /event_stored|event_rejected/ | parse @message /(?<outcome>event_stored|event_rejected)/ | stats count() by outcome, bin(5m)"
        }
      },
      {
        type = "log", x = 12, y = 20, width = 12, height = 6
        properties = {
          title  = "Latest refused requests (who tried what)"
          region = var.aws_region
          view   = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.query.name}' | fields @timestamp, @message | filter @message like /access_denied/ | sort @timestamp desc | limit 10"
        }
      },
    ]
  })
}
