import os
import subprocess
import sys
from pathlib import Path

import yaml

from app.factory import create_app


def test_cancellation_openapi_declares_required_csrf_and_origin_fallback(auth_settings):
    operation = create_app(auth_settings).openapi()["paths"][
        "/api/v1/appointments/{appointment_id}/cancel"
    ]["post"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert "X-CSRF-Token" in parameters
    csrf = parameters["X-CSRF-Token"]
    assert csrf["in"] == "header"
    assert csrf["required"] is True
    assert "prl_csrf" in csrf["description"]
    assert parameters["Origin"]["required"] is False
    assert parameters["Referer"]["required"] is False
    assert "Referer" in parameters["Origin"]["description"]
    assert "Origin" in parameters["Referer"]["description"]
    assert "Origin or Referer" in operation["description"]
    assert "Origin takes precedence" in operation["description"]
    assert operation["requestBody"]["required"] is True


def test_openapi_export_is_deterministic_without_database(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "openapi.yaml"
    env = dict(os.environ, DATABASE_URL="mysql+asyncmy://user:password@127.0.0.1:1/unavailable")
    command = [
        sys.executable,
        str(root / "tools/scripts/export_openapi.py"),
        "--output",
        str(output),
    ]
    subprocess.run(command, cwd=root, env=env, check=True, capture_output=True, text=True)
    first = output.read_bytes()
    subprocess.run(command, cwd=root, env=env, check=True, capture_output=True, text=True)
    assert output.read_bytes() == first
    document = yaml.safe_load(first)
    assert set(document["paths"]) == {
        "/health/live",
        "/health/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/auth/csrf",
        "/api/v1/auth/logout",
        "/api/v1/purposes",
        "/api/v1/staff",
        "/api/v1/availability",
        "/api/v1/appointments",
        "/api/v1/appointments/{appointment_id}",
        "/api/v1/appointments/{appointment_id}/cancel",
        "/api/v1/booking-context",
        "/api/v1/students/me",
        "/api/v1/staff/me/appointments",
        "/api/v1/staff/me/appointments/{appointment_id}",
        "/api/v1/staff/me/appointments/{appointment_id}/cancel",
        "/api/v1/staff/me/appointments/{appointment_id}/outcome",
        "/api/v1/staff/me/appointments/{appointment_id}/change-proposals",
        "/api/v1/appointments/{appointment_id}/change-proposals",
        "/api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/accept",
        "/api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/decline",
    }
    staff_list = document["paths"]["/api/v1/staff/me/appointments"]["get"]
    assert staff_list["security"] == [{"prl_session": []}]
    staff_parameters = {parameter["name"]: parameter for parameter in staff_list["parameters"]}
    assert {"page", "page_size", "status", "date"} <= staff_parameters.keys()
    outcome = document["paths"]["/api/v1/staff/me/appointments/{appointment_id}/outcome"]["post"]
    assert "APPOINTMENT_NOT_ENDED" in outcome["description"]
    assert "X-CSRF-Token" in {parameter["name"] for parameter in outcome["parameters"]}
    schemes = document["components"]["securitySchemes"]
    assert any(
        s["type"] == "apiKey" and s["in"] == "cookie" and s["name"] == "prl_session"
        for s in schemes.values()
    )
    for path in (
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/auth/csrf",
        "/api/v1/auth/logout",
    ):
        method = "get" if path.endswith(("/me", "/csrf")) else "post"
        assert "ErrorResponse" in str(document["paths"][path][method]["responses"])
    for path in ("/api/v1/purposes", "/api/v1/staff"):
        operation = document["paths"][path]["get"]
        assert operation["security"] == [{"prl_session": []}]
        assert "ErrorResponse" in str(operation["responses"])
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert {"page", "page_size"} <= parameters.keys()
        page = parameters["page"]["schema"]
        page_size = parameters["page_size"]["schema"]
        assert page["default"] == 1
        assert page["minimum"] == 1
        assert page_size["default"] == 20
        assert page_size["minimum"] == 1
        assert page_size["maximum"] == 100
        if path.endswith("/staff"):
            purpose_id = next(p for p in operation["parameters"] if p["name"] == "purpose_id")
            assert purpose_id["schema"]["format"] == "uuid"
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert (
            response_schema["$ref"].endswith("PageResponse_PurposeItem_")
            if path.endswith("/purposes")
            else response_schema["$ref"].endswith("PageResponse_StaffItem_")
        )
        for status in ("401", "403", "404", "422", "500", "503"):
            assert {"Cache-Control", "X-Error-Class", "X-Request-ID"} <= set(
                operation["responses"][status]["headers"]
            )
    availability = document["paths"]["/api/v1/availability"]["get"]
    assert availability["security"] == [{"prl_session": []}]
    availability_parameters = {
        parameter["name"]: parameter for parameter in availability["parameters"]
    }
    assert set(availability_parameters) == {"purpose_id", "staff_id", "date"}
    assert availability_parameters["purpose_id"]["required"] is True
    assert availability_parameters["staff_id"]["required"] is True
    assert availability_parameters["date"]["required"] is True
    assert availability_parameters["purpose_id"]["schema"]["format"] == "uuid"
    assert availability_parameters["staff_id"]["schema"]["format"] == "uuid"
    assert availability_parameters["date"]["schema"]["format"] == "date"
    availability_schema_ref = availability["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert availability_schema_ref.endswith("AvailabilityResponse")
    schemas = document["components"]["schemas"]
    availability_schema = schemas[availability_schema_ref.rsplit("/", 1)[-1]]
    assert set(availability_schema["properties"]) == {"date", "timezone", "slots"}
    assert availability_schema["properties"]["date"]["format"] == "date"
    slot_schema_ref = availability_schema["properties"]["slots"]["items"]["$ref"]
    slot_schema = schemas[slot_schema_ref.rsplit("/", 1)[-1]]
    assert set(slot_schema["properties"]) == {"starts_at", "ends_at"}
    for property_name in ("starts_at", "ends_at"):
        timestamp_ref = slot_schema["properties"][property_name]["$ref"]
        timestamp_schema = schemas[timestamp_ref.rsplit("/", 1)[-1]]
        assert timestamp_schema["format"] == "date-time"
    assert "student_id" not in availability_parameters
    assert not {"slot_id", "student_id", "other_reason"} & set(schemas)
    for status in ("401", "403", "404", "422", "500", "503"):
        response = availability["responses"][status]
        assert "ErrorResponse" in str(response["content"])
        assert {"Cache-Control", "X-Error-Class", "X-Request-ID"} <= set(response["headers"])
    appointments = document["paths"]["/api/v1/appointments"]["post"]
    assert appointments["security"] == [{"prl_session": []}]
    appointment_parameters = {p["name"]: p for p in appointments["parameters"]}
    assert appointment_parameters["Idempotency-Key"]["in"] == "header"
    assert appointment_parameters["Idempotency-Key"]["required"] is True
    assert appointment_parameters["Idempotency-Key"]["schema"]["maxLength"] == 128
    assert appointment_parameters["Idempotency-Key"]["schema"]["pattern"] == r"\S"
    assert appointment_parameters["X-CSRF-Token"]["required"] is True
    assert "prl_csrf" in appointment_parameters["X-CSRF-Token"]["description"]
    assert appointment_parameters["Origin"]["required"] is False
    assert appointment_parameters["Referer"]["required"] is False
    assert "Origin or Referer" in appointments["description"]
    assert "Origin takes precedence" in appointments["description"]
    assert "Referer" in appointment_parameters["Origin"]["description"]
    assert "Origin" in appointment_parameters["Referer"]["description"]
    assert appointments["requestBody"]["required"] is True
    assert "student_id" not in str(appointments["requestBody"])
    for status in ("200", "201"):
        assert appointments["responses"][status]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("AppointmentCreateResponse")
    for status in ("401", "403", "404", "409", "422", "500", "503"):
        response = appointments["responses"][status]
        assert "ErrorResponse" in str(response["content"])
        assert {"Cache-Control", "X-Error-Class", "X-Request-ID"} <= set(response["headers"])
    detail = document["paths"]["/api/v1/appointments/{appointment_id}"]["get"]
    assert detail["security"] == [{"prl_session": []}]
    detail_parameters = {p["name"]: p for p in detail["parameters"]}
    assert detail_parameters["appointment_id"]["required"] is True
    assert detail_parameters["appointment_id"]["schema"]["format"] == "uuid"
    detail_schema_ref = detail["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert detail_schema_ref.endswith("AppointmentDetailResponse")
    detail_schema = schemas[detail_schema_ref.rsplit("/", 1)[-1]]
    assert set(detail_schema["properties"]) == {
        "id",
        "purpose_id",
        "purpose_code",
        "purpose_name",
        "staff_id",
        "staff_name",
        "duration_minutes",
        "starts_at",
        "ends_at",
        "description",
        "location",
        "status",
        "created_at",
        "cancelled_at",
        "cancel_reason",
    }
    assert "creation_request_hash" not in str(schemas)
    for status in ("401", "403", "404", "422", "500", "503"):
        response = detail["responses"][status]
        assert "ErrorResponse" in str(response["content"])
        assert {"Cache-Control", "X-Error-Class", "X-Request-ID"} <= set(response["headers"])
    assert "Retry-After" in str(document["paths"]["/api/v1/auth/login"]["post"]["responses"]["429"])
    assert "200" in document["paths"]["/health/live"]["get"]["responses"]
    assert "503" in document["paths"]["/health/ready"]["get"]["responses"]
    assert "ErrorResponse" in document["components"]["schemas"]
    for path, statuses in {"/health/live": ("500",), "/health/ready": ("500", "503")}.items():
        for status in statuses:
            headers = document["paths"][path]["get"]["responses"][status]["headers"]
            assert "X-Error-Class" in headers


def test_openapi_export_ignores_invalid_runtime_environment(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "openapi.yaml"
    env = dict(
        os.environ,
        TRUSTED_GATEWAY_CIDRS="not-a-cidr",
        ENVIRONMENT="production",
        COOKIE_SECURE="false",
        READINESS_TIMEOUT_SECONDS="inf",
    )
    result = subprocess.run(
        [sys.executable, str(root / "tools/scripts/export_openapi.py"), "--output", str(output)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(output.read_text(encoding="utf-8"))["paths"]
