"""HTTP API contracts."""

from fastapi import APIRouter

API_V1_PREFIX = "/api/v1"
api_v1_router = APIRouter(prefix=API_V1_PREFIX)


def _register_auth() -> None:
    from app.api.auth import router

    api_v1_router.include_router(router)


_register_auth()
