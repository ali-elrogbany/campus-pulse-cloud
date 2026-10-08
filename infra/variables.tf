variable "aws_region" {
  description = "AWS region to deploy to."
  type        = string
  default     = "eu-west-3" # Paris
}

variable "project_name" {
  description = "Prefix used in the names of all resources."
  type        = string
  default     = "campuspulse"
}

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string
}

variable "create_budget" {
  description = "Create the AWS Budget from Terraform. Set to false if you already created one in the console."
  type        = bool
  default     = true
}

variable "monthly_budget_usd" {
  description = "Monthly budget in USD. Alerts fire at 80% actual and 100% forecasted."
  type        = number
  default     = 5
}

variable "vpc_cidr" {
  description = "IP range of the private network. The two private subnets are carved out of it."
  type        = string
  default     = "10.0.0.0/16"
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps Lambda logs. Short retention keeps logging inside the free tier."
  type        = number
  default     = 14
}

variable "lambda_memory_mb" {
  description = "Memory per Lambda function. 128 MB is the cheapest and enough for this workload."
  type        = number
  default     = 128
}

variable "event_ttl_days" {
  description = "Events are deleted automatically (DynamoDB TTL) after this many days."
  type        = number
  default     = 30
}

variable "api_rate_limit" {
  description = "Steady-state requests per second allowed by the API usage plan."
  type        = number
  default     = 10
}

variable "api_burst_limit" {
  description = "Burst requests allowed by the API usage plan."
  type        = number
  default     = 20
}

variable "api_daily_quota" {
  description = "Maximum requests per day for the simulator API key. A hard cap on cost."
  type        = number
  default     = 10000
}
