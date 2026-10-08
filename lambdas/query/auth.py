"""Role-based access control (RBAC) for the read API.

API Gateway has already verified the user's login token (signature, expiry) before our code
runs. The token's claims arrive in event["requestContext"]["authorizer"]["claims"]. We
trust those claims, because only API Gateway can set them, and use the "cognito:groups"
claim to decide what this user may do.

Pure Python with no AWS imports, so it is unit-tested directly.
"""
from __future__ import annotations

OPS_ADMIN = "ops_admin"
STUDENT = "student"

# Which roles may call which route. A route missing from this table is denied to everyone.
ROUTE_ROLES = {
    "/events": {OPS_ADMIN, STUDENT},
    "/stats": {OPS_ADMIN, STUDENT},
    "/alerts": {OPS_ADMIN},  # operational data: staff only
}


def get_claims(event: dict) -> dict:
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}


def parse_groups(raw) -> set[str]:
    """Turn the 'cognito:groups' claim into a set of group names.

    API Gateway delivers it as a string whose shape depends on the number of groups:
    "ops_admin" for one group, "[ops_admin student]" for several (sometimes comma-separated).
    Be liberal in what we accept, and strict in what we allow.
    """
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(g) for g in raw}
    text = str(raw).strip().strip("[]")
    return {part for part in text.replace(",", " ").split() if part}


def is_allowed(route: str, groups: set[str]) -> bool:
    return bool(ROUTE_ROLES.get(route, set()) & groups)
