output "api_url" {
  description = "Base URL of the API (append /events)."
  value       = aws_api_gateway_stage.v1.invoke_url
}

output "api_key" {
  description = "API key for the simulator. Sensitive: read it with `terraform output -raw api_key`."
  value       = aws_api_gateway_api_key.simulator.value
  sensitive   = true
}

output "table_name" {
  description = "Name of the DynamoDB events table."
  value       = aws_dynamodb_table.events.name
}
