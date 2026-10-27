"""Internal Prometheus endpoint backed by the app-owned registry."""

from fastapi import Request
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.responses import Response


def metrics_response(request: Request) -> Response:
    telemetry = getattr(request.app.state, "telemetry", None)
    body = telemetry.metrics.render() if telemetry is not None else ""
    return Response(content=body, headers={"Content-Type": CONTENT_TYPE_LATEST})
