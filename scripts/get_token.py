#!/usr/bin/env python3
"""Log in to CampusPulse and print the login token (a JWT "ID token").

You send this token in the Authorization header of every API call. It expires after
60 minutes, then just run this script again.

Example:
    export CAMPUSPULSE_CLIENT_ID=$(terraform -chdir=infra output -raw user_pool_client_id)
    TOKEN=$(python scripts/get_token.py alice@example.com)
    curl -H "Authorization: $TOKEN" "$CAMPUSPULSE_URL/alerts"
"""
import argparse
import getpass
import os
import sys

import boto3
from botocore.exceptions import ClientError


def get_id_token(client, client_id: str, email: str, password: str) -> str:
    response = client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": password},
    )
    return response["AuthenticationResult"]["IdToken"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a CampusPulse login token")
    parser.add_argument("email")
    parser.add_argument("--client-id", default=os.environ.get("CAMPUSPULSE_CLIENT_ID"))
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "eu-west-3"))
    args = parser.parse_args()

    if not args.client_id:
        parser.error("set --client-id or CAMPUSPULSE_CLIENT_ID")

    # getpass prompts on the terminal (not stdout), so TOKEN=$(...) captures only the token.
    password = getpass.getpass(f"Password for {args.email}: ")

    # This call needs no AWS credentials: the user proves who they are with their password.
    client = boto3.client("cognito-idp", region_name=args.region)
    try:
        token = get_id_token(client, args.client_id, args.email, password)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "NotAuthorizedException":
            print("Login failed: wrong email or password.", file=sys.stderr)
        else:
            print(f"Login failed: {code}: {exc.response['Error']['Message']}", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
