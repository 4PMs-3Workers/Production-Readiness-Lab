"""HTTP API contracts."""

from fastapi import APIRouter

API_V1_PREFIX = "/api/v1"
api_v1_router = APIRouter(prefix=API_V1_PREFIX)
