# =============================================================================
# Authentication and authorization (slice 2)
#
#   Cognito user pool  = the user directory: stores accounts, checks passwords,
#                        and issues signed login tokens (JWTs).
#   Groups             = the two roles: ops_admin and student.
#   API authorizer     = API Gateway verifies the token's signature and expiry
#                        BEFORE any Lambda runs. Requests without a valid token
#                        get 401 and never reach our code.
#   The Lambda then reads the user's group from the verified token and decides
#   which routes that role may use (see lambdas/query/auth.py).
# =============================================================================

resource "aws_cognito_user_pool" "main" {
  name = "${var.project_name}-users"

  # Users sign in with their email address.
  username_attributes = ["email"]

  # No public sign-up: only an administrator can create accounts. This stops
  # strangers from registering themselves and calling our API.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # Only an administrator can reset passwords (no self-service email flow to set up).
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }
}

# The two roles. A user's group list is embedded in their token as "cognito:groups".
resource "aws_cognito_user_group" "ops_admin" {
  name         = "ops_admin"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Campus operations staff: events, stats and alerts"
}

resource "aws_cognito_user_group" "student" {
  name         = "student"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Students: events and stats only"
}

# An "app client" is how an application (our scripts, later the dashboard) is
# allowed to log users in. No client secret, because a browser app cannot keep one.
resource "aws_cognito_user_pool_client" "app" {
  name         = "${var.project_name}-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  # USER_PASSWORD_AUTH lets a script send email + password and receive tokens.
  # Fine for this prototype over HTTPS; a production web app would use the
  # hosted login page with the authorization-code flow instead.
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  # Wrong email and wrong password give the same error, so attackers cannot
  # discover which emails have accounts.
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  # Short-lived tokens limit the damage if one leaks.
  id_token_validity      = 60
  access_token_validity  = 60
  refresh_token_validity = 1

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }
}

# Plugs the user pool into API Gateway. The token goes in the Authorization header.
resource "aws_api_gateway_authorizer" "cognito" {
  name            = "cognito-authorizer"
  rest_api_id     = aws_api_gateway_rest_api.api.id
  type            = "COGNITO_USER_POOLS"
  provider_arns   = [aws_cognito_user_pool.main.arn]
  identity_source = "method.request.header.Authorization"
}
