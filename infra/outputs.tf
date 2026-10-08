output "api_url" {
  description = "Base URL of the API (append /events)."
  value       = aws_api_gateway_stage.v1.invoke_url
}

output "api_key" {
  description = "API key for the simulator. Sensitive: read it with `terraform output -raw api_key`."
  value       = aws_api_gateway_api_key.simulator.value
  sensitive   = true
}

output "user_pool_id" {
  description = "Cognito user pool ID (used by scripts/create_user.py)."
  value       = aws_cognito_user_pool.main.id
}

output "user_pool_client_id" {
  description = "Cognito app client ID (used by scripts/get_token.py)."
  value       = aws_cognito_user_pool_client.app.id
}

output "aws_region" {
  description = "Region everything was deployed to."
  value       = var.aws_region
}

output "dashboard_url" {
  description = "Open this in your browser to see the CloudWatch operations dashboard."
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "vpc_id" {
  description = "ID of the private network the Lambdas run in."
  value       = aws_vpc.main.id
}

output "table_name" {
  description = "Name of the DynamoDB events table."
  value       = aws_dynamodb_table.events.name
}
