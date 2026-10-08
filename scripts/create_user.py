#!/usr/bin/env python3
"""Create a CampusPulse user in Cognito and put them in a role (group).

Passwords are typed at a hidden prompt, so they never appear in your shell history,
in Terraform's state file, or in the repository.

Example:
    export CAMPUSPULSE_USER_POOL_ID=$(terraform -chdir=infra output -raw user_pool_id)
    python scripts/create_user.py alice@example.com ops_admin
    python scripts/create_user.py bob@example.com student
"""
import argparse
import getpass
import os
import sys

import boto3
from botocore.exceptions import ClientError

GROUPS = ("ops_admin", "student")


def create_user(client, pool_id: str, email: str, group: str, password: str) -> bool:
    """Create (or update) the user, set a permanent password, add to group.

    Returns True if the account was newly created, False if it already existed.
    """
    created = True
    try:
        client.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",  # do not send an invitation email
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise
        created = False

    # Permanent=True skips the "you must change your temporary password" step.
    client.admin_set_user_password(
        UserPoolId=pool_id, Username=email, Password=password, Permanent=True
    )
    client.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=group)
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a CampusPulse user")
    parser.add_argument("email")
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("--user-pool-id", default=os.environ.get("CAMPUSPULSE_USER_POOL_ID"))
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "eu-west-3"))
    args = parser.parse_args()

    if not args.user_pool_id:
        parser.error("set --user-pool-id or CAMPUSPULSE_USER_POOL_ID")

    password = getpass.getpass("Password (min 12 chars, upper, lower, digit, symbol): ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1

    client = boto3.client("cognito-idp", region_name=args.region)
    try:
        created = create_user(client, args.user_pool_id, args.email, args.group, password)
    except ClientError as exc:
        error = exc.response["Error"]
        print(f"AWS refused: {error['Code']}: {error['Message']}", file=sys.stderr)
        return 1

    print(f"{'Created' if created else 'Updated existing'} user {args.email} in group {args.group}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
