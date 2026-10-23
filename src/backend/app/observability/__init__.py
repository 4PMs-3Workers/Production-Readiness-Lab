"""Safe, process-owned observability primitives."""

from app.observability.runtime import TelemetryRuntime, create_telemetry

__all__ = ["TelemetryRuntime", "create_telemetry"]
