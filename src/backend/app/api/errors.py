"""Stable, sanitized HTTP errors with service-level classification."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette._utils import get_route_path
from starlette.exceptions import HTTPException

from app.core.errors import AppError as AppError
from app.core.errors import BusinessConflict as BusinessConflict
from app.core.errors import ErrorClass as ErrorClass
from app.core.errors import ServiceUnavailable as ServiceUnavailable
from app.core.request_context import get_request_id
from app.core.response_policy import requires_private_response
from app.schemas.common import ErrorDetail, ErrorResponse


def error_response(request: Request, error: AppError) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=error.code, message=error.message, request_id=get_request_id(request)
        )
    )
    headers = dict(error.headers or {})
    route_path = get_route_path(request.scope)
    if route_path in {"/health/live", "/health/ready"}:
        headers["Cache-Control"] = "no-store"
    elif requires_private_response(request.scope):
        headers["Cache-Control"] = "private, no-store"
    headers["X-Error-Class"] = error.error_class
    headers["X-Request-ID"] = get_request_id(request)
    return JSONResponse(status_code=error.status_code, content=body.model_dump(), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            error_class: ErrorClass = "service"
            code = "SERVICE_UNAVAILABLE" if exc.status_code == 503 else "INTERNAL_SERVER_ERROR"
            message = (
                "Service temporarily unavailable"
                if exc.status_code == 503
                else "Internal server error"
            )
        else:
            error_class = "input"
            code = (
                "NOT_FOUND"
                if exc.status_code == 404
                else "METHOD_NOT_ALLOWED" if exc.status_code == 405 else "HTTP_ERROR"
            )
            message = (
                "Not found"
                if exc.status_code == 404
                else "Method not allowed" if exc.status_code == 405 else "Request rejected"
            )
        return error_response(
            request, AppError(exc.status_code, code, message, error_class, exc.headers)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            request, AppError(422, "VALIDATION_ERROR", "Invalid request", "input")
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(
            request,
            AppError(500, "INTERNAL_SERVER_ERROR", "Internal server error", "service"),
        )
