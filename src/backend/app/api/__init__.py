"""HTTP API contracts."""

from fastapi import APIRouter

API_V1_PREFIX = "/api/v1"
api_v1_router = APIRouter(prefix=API_V1_PREFIX)


def _register_auth() -> None:
    from app.api.auth import router

    api_v1_router.include_router(router)


_register_auth()


def _register_catalog() -> None:
    from app.api.catalog import router

    api_v1_router.include_router(router)


_register_catalog()


def _register_availability() -> None:
    from app.api.availability import router

    api_v1_router.include_router(router)


_register_availability()


def _register_appointments() -> None:
    from app.api.appointments import router

    api_v1_router.include_router(router)


_register_appointments()


def _register_student_profile() -> None:
    from app.api.student_profile import router

    api_v1_router.include_router(router)


_register_student_profile()


def _register_staff_appointments() -> None:
    from app.api.staff_appointments import router

    api_v1_router.include_router(router)


_register_staff_appointments()
