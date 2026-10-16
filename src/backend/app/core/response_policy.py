"""Path predicates shared by response middleware and outer error fallback."""

from starlette._utils import get_route_path
from starlette.types import Scope

CATALOG_PATHS = frozenset({"/api/v1/purposes", "/api/v1/staff", "/api/v1/availability"})


def is_catalog_path(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = get_route_path(scope).rstrip("/") or "/"
    return path in CATALOG_PATHS


def requires_private_response(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = get_route_path(scope)
    return path.startswith("/api/v1/auth/") or is_catalog_path(scope)
