"""Path predicates shared by response middleware and outer error fallback."""

from starlette._utils import get_route_path
from starlette.types import Scope

CATALOG_PATHS = frozenset(
    {"/api/v1/purposes", "/api/v1/staff", "/api/v1/availability", "/api/v1/booking-context"}
)
APPOINTMENT_COLLECTION_PATH = "/api/v1/appointments"
STAFF_APPOINTMENT_PREFIX = "/api/v1/staff/me/appointments"


def is_catalog_path(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = get_route_path(scope).rstrip("/") or "/"
    return path in CATALOG_PATHS


def is_appointment_path(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = get_route_path(scope).rstrip("/") or "/"
    return path == APPOINTMENT_COLLECTION_PATH or path.startswith(APPOINTMENT_COLLECTION_PATH + "/")


def is_staff_appointment_path(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = get_route_path(scope).rstrip("/") or "/"
    return path == STAFF_APPOINTMENT_PREFIX or path.startswith(STAFF_APPOINTMENT_PREFIX + "/")


def uses_private_api_middleware(scope: Scope) -> bool:
    return is_catalog_path(scope) or is_appointment_path(scope) or is_staff_appointment_path(scope)


def requires_request_deadline(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    method = scope["method"]
    return (
        (is_catalog_path(scope) and method == "GET")
        or (is_appointment_path(scope) and method in {"GET", "POST"})
        or (is_staff_appointment_path(scope) and method in {"GET", "POST"})
    )


def requires_private_response(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = get_route_path(scope)
    return path.startswith("/api/v1/auth/") or uses_private_api_middleware(scope)
