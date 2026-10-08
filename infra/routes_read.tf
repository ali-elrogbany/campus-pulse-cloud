# =============================================================================
# Read routes (slice 2): GET /stats and GET /alerts.
# GET /events lives in main.tf. All three are served by the same "query" Lambda,
# which looks at which route was called and applies the role rules.
# Every route here requires a valid Cognito token (the authorizer).
# =============================================================================

resource "aws_api_gateway_resource" "stats" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "stats"
}

resource "aws_api_gateway_resource" "alerts" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "alerts"
}

# ---- GET /stats -------------------------------------------------------------
resource "aws_api_gateway_method" "get_stats" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.stats.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "get_stats" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.stats.id
  http_method             = aws_api_gateway_method.get_stats.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query.invoke_arn
}

resource "aws_lambda_permission" "query_from_api_stats" {
  statement_id  = "AllowInvokeFromApiGatewayStats"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/stats"
}

# ---- GET /alerts ------------------------------------------------------------
resource "aws_api_gateway_method" "get_alerts" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.alerts.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "get_alerts" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.alerts.id
  http_method             = aws_api_gateway_method.get_alerts.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query.invoke_arn
}

resource "aws_lambda_permission" "query_from_api_alerts" {
  statement_id  = "AllowInvokeFromApiGatewayAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/alerts"
}
