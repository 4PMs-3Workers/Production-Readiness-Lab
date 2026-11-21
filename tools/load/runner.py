"""Real-cookie, CSRF-protected concurrent student/staff workload over the gateway."""

import asyncio
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from tools.load.config import LoadConfig, validate_load_config

_BUSINESS_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class SessionCredential:
    user_id: UUID
    email: str
    role: str
    appointment_id: UUID | None = None


@dataclass(frozen=True)
class WorkloadFixture:
    purpose_id: UUID
    students: list[SessionCredential]
    staff: list[SessionCredential]
    proposal_appointments: list[UUID]
    password: str = field(repr=False)


@dataclass
class WorkloadResult:
    requested_sessions: int
    authenticated_sessions: int
    duration_seconds: float
    started_at: datetime
    ended_at: datetime
    expected_gateway_requests: int = 0
    gateway_request_ids: set[str] = field(default_factory=set, repr=False)
    operation_counts: Counter[str] = field(default_factory=Counter)
    event_loop_lag_seconds: list[float] = field(default_factory=list)
    created_booking_ids: list[UUID] = field(default_factory=list)
    cancelled_appointment_ids: list[UUID] = field(default_factory=list)
    created_proposal_ids: list[UUID] = field(default_factory=list)
    verified_replay_count: int = 0
    idempotency_failures: int = 0
    ambiguous_write_failures: int = 0

    @property
    def generator_saturated(self) -> bool:
        if len(self.event_loop_lag_seconds) < 2:
            return True
        ordered = sorted(self.event_loop_lag_seconds)
        p95 = ordered[max(0, int(0.95 * len(ordered)) - 1)]
        missed_ticks = sum(lag > 0.1 for lag in ordered)
        return p95 > 0.1 or missed_ticks / len(ordered) > 0.1


@dataclass
class _Session:
    credential: SessionCredential
    client: httpx.AsyncClient
    csrf_token: str
    appointments: list[UUID] = field(default_factory=list)


def load_workload_fixture(seed: int, run_id: str, password: str) -> WorkloadFixture:
    """Build the credential inventory matching the separately provisioned schema."""
    from tools.load.fixtures import make_fixture_rows

    rows = make_fixture_rows(
        seed=seed,
        generated_at=datetime.now(UTC),
        password_hash="not-used-for-http-client-inventory",
        run_id=run_id,
    )
    return WorkloadFixture(
        purpose_id=rows.purpose.id,
        students=[
            SessionCredential(
                row.id,
                row.email,
                "STUDENT",
                rows.appointments[10 + index].id,
            )
            for index, row in enumerate(rows.students[10:20])
        ],
        staff=[SessionCredential(row.id, row.email, "STAFF") for row in rows.staff],
        proposal_appointments=[row.id for row in rows.appointments[:10]],
        password=password,
    )


async def run_workload(
    config: LoadConfig,
    fixture: WorkloadFixture,
    *,
    seed: int,
    duration_seconds: int | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> WorkloadResult:
    """Warm 20 real sessions, then run a deterministic operation mix for the window."""
    validate_load_config(config)
    duration = config.duration_seconds if duration_seconds is None else duration_seconds
    if not 1 <= duration <= 600:
        raise ValueError("duration_seconds must be between 1 and 600")
    credentials = (fixture.students + fixture.staff)[: config.sessions]
    if len(credentials) != config.sessions:
        raise ValueError("Fixture does not contain the configured number of sessions")
    clients: list[httpx.AsyncClient] = []
    sessions: list[_Session] = []
    request_counter = {"count": 0}
    gateway_request_ids: set[str] = set()

    async def count_request(_request: httpx.Request) -> None:
        request_counter["count"] += 1

    async def capture_request_id(response: httpx.Response) -> None:
        request_id = response.headers.get("X-Request-ID")
        if request_id:
            gateway_request_ids.add(request_id)

    origin = config.target_url.rstrip("/")
    try:
        for index, credential in enumerate(credentials):
            client = client_factory(
                base_url=origin,
                timeout=15,
                follow_redirects=False,
                headers={"Origin": origin},
                event_hooks={"request": [count_request], "response": [capture_request_id]},
            )
            clients.append(client)
            sessions.append(await _authenticate(client, credential, fixture.password))
            if index + 1 < len(credentials):
                await asyncio.sleep(2)
        request_counter["count"] = 0
        gateway_request_ids.clear()
        result = WorkloadResult(
            requested_sessions=len(credentials),
            authenticated_sessions=len(sessions),
            duration_seconds=duration,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            gateway_request_ids=gateway_request_ids,
        )
        deadline = time.monotonic() + duration
        monitor = asyncio.create_task(_monitor_event_loop(result, deadline))
        tasks = [
            asyncio.create_task(
                _run_session(
                    index,
                    session,
                    fixture,
                    config,
                    seed,
                    deadline,
                    result,
                    next((item for item in sessions if item.credential.role == "STUDENT"), None),
                )
            )
            for index, session in enumerate(sessions)
        ]
        await asyncio.gather(*tasks)
        result.ended_at = datetime.now(UTC)
        result.expected_gateway_requests = request_counter["count"]
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        return result
    finally:
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


async def _authenticate(
    client: httpx.AsyncClient, credential: SessionCredential, password: str
) -> _Session:
    response = await client.post(
        "/api/v1/auth/login", json={"email": credential.email, "password": password}
    )
    if response.status_code != 200:
        raise RuntimeError("Candidate rejected a fictional load-session login")
    try:
        actual_user = UUID(response.json()["id"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Candidate returned an invalid load-session identity") from None
    if actual_user != credential.user_id:
        raise RuntimeError("Candidate session identity did not match the fixture")
    csrf_response = await client.get("/api/v1/auth/csrf")
    if csrf_response.status_code != 200:
        raise RuntimeError("Candidate rejected a fictional load-session CSRF refresh")
    csrf_token = csrf_response.json().get("csrf_token")
    if not isinstance(csrf_token, str) or not csrf_token:
        raise RuntimeError("Candidate returned an invalid CSRF token")
    client.headers["X-CSRF-Token"] = csrf_token
    appointments = [credential.appointment_id] if credential.appointment_id is not None else []
    return _Session(credential, client, csrf_token, appointments)


async def confirm_fixture_target(
    target_url: str,
    fixture: WorkloadFixture,
    *,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> None:
    """Canary login proves the API uses this run's isolated fixture schema."""
    origin = target_url.rstrip("/")
    credential = fixture.students[0]
    async with client_factory(
        base_url=origin,
        timeout=10,
        follow_redirects=False,
        headers={"Origin": origin},
    ) as client:
        await _authenticate(client, credential, fixture.password)
        current = await client.get("/api/v1/auth/me")
        if current.status_code != 200 or UUID(current.json().get("id", "")) != credential.user_id:
            raise RuntimeError("Candidate API is not connected to the owned load fixture schema")
        logout = await client.post("/api/v1/auth/logout", json={})
        if logout.status_code != 204:
            raise RuntimeError("Candidate could not revoke the isolated fixture canary session")


async def _run_session(
    index: int,
    session: _Session,
    fixture: WorkloadFixture,
    config: LoadConfig,
    seed: int,
    deadline: float,
    result: WorkloadResult,
    availability_session: _Session | None,
) -> None:
    rng = random.Random(seed + index)
    first_operation = True
    while time.monotonic() < deadline:
        operation = (
            "proposal"
            if first_operation and session.credential.role == "STAFF"
            else _choose_operation(session.credential.role, rng.random())
        )
        first_operation = False
        result.operation_counts[f"{operation}.attempted"] += 1
        try:
            outcome = await _perform_operation(
                operation, session, fixture, config, rng, result, availability_session
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError):
            outcome = "client_or_contract_error"
        result.operation_counts[f"{operation}.{outcome}"] += 1
        await asyncio.sleep(rng.uniform(0.5, 1.25))


def _choose_operation(role: str, value: float) -> str:
    if role == "STUDENT":
        return "read" if value < 0.5 else "book" if value < 0.75 else "cancel"
    return "read" if value < 0.5 else "proposal"


async def _perform_operation(
    operation: str,
    session: _Session,
    fixture: WorkloadFixture,
    config: LoadConfig,
    rng: random.Random,
    result: WorkloadResult,
    availability_session: _Session | None = None,
) -> str:
    if operation == "read":
        route = (
            "/api/v1/appointments?page=1&page_size=20"
            if session.credential.role == "STUDENT"
            else "/api/v1/staff/me/appointments?page=1&page_size=20"
        )
        response = await session.client.get(route)
        return _http_outcome(response.status_code, expected={200})
    if operation == "book":
        return await _book(session, fixture, rng, result)
    if operation == "cancel":
        return await _cancel(session, result)
    if operation == "proposal":
        return await _propose(session, fixture, rng, result, availability_session)
    raise ValueError("Unsupported workload operation")


async def _book(
    session: _Session,
    fixture: WorkloadFixture,
    rng: random.Random,
    result: WorkloadResult | None = None,
) -> str:
    staff_index = rng.randrange(10)
    selected_date = datetime.now(_BUSINESS_TIMEZONE).date() + timedelta(days=rng.randint(1, 6))
    query = {
        "purpose_id": str(fixture.purpose_id),
        "staff_id": str(fixture.staff[staff_index].user_id),
        "date": selected_date.isoformat(),
    }
    availability = await session.client.get("/api/v1/availability", params=query)
    if availability.status_code != 200:
        return _http_outcome(availability.status_code, expected={200})
    slots = availability.json().get("slots", [])
    if not slots:
        return "no_available_slot"
    selected = slots[rng.randrange(len(slots))]["starts_at"]
    body = {
        "purpose_id": str(fixture.purpose_id),
        "staff_id": query["staff_id"],
        "starts_at": selected,
    }
    key = str(uuid4())
    route = "/api/v1/appointments"
    headers = {"Idempotency-Key": key}
    prior_attempt_ambiguous = False
    try:
        created = await session.client.post(route, json=body, headers=headers)
    except httpx.HTTPError:
        prior_attempt_ambiguous = True
        try:
            created = await session.client.post(route, json=body, headers=headers)
        except httpx.HTTPError:
            if result is not None:
                result.ambiguous_write_failures += 1
            return "ambiguous_write"
    if created.status_code == 409:
        if prior_attempt_ambiguous and result is not None:
            result.ambiguous_write_failures += 1
        return "business_conflict"
    if created.status_code not in {200, 201}:
        if created.status_code < 500:
            if prior_attempt_ambiguous and result is not None:
                result.ambiguous_write_failures += 1
            return _http_outcome(created.status_code, expected={200, 201})
        prior_attempt_ambiguous = True
        try:
            created = await session.client.post(route, json=body, headers=headers)
        except httpx.HTTPError:
            if result is not None:
                result.ambiguous_write_failures += 1
            return "ambiguous_write"
    receipt = _receipt(created, ("id", "status", "created_at"))
    if receipt is None:
        try:
            created = await session.client.post(route, json=body, headers=headers)
        except httpx.HTTPError:
            if result is not None:
                result.ambiguous_write_failures += 1
            return "ambiguous_write"
        receipt = _receipt(created, ("id", "status", "created_at"))
    if receipt is None or receipt["status"] != "UPCOMING":
        if result is not None:
            result.ambiguous_write_failures += 1
        return "ambiguous_write"
    appointment_id = UUID(str(receipt["id"]))
    session.appointments.append(appointment_id)
    if result is not None:
        result.created_booking_ids.append(appointment_id)
    try:
        replay = await session.client.post(route, json=body, headers=headers)
    except httpx.HTTPError:
        replay = None
    if not _same_receipt(replay, receipt):
        if result is not None:
            result.idempotency_failures += 1
        return "idempotency_replay_failed"
    if result is not None:
        result.verified_replay_count += 1
    return "success"


async def _cancel(session: _Session, result: WorkloadResult | None = None) -> str:
    if not session.appointments:
        return "no_owned_appointment"
    appointment_id = session.appointments.pop(0)
    try:
        response = await session.client.post(
            f"/api/v1/appointments/{appointment_id}/cancel", json={}
        )
    except httpx.HTTPError:
        if result is not None:
            # A lost response cannot tell us whether the transaction committed.
            result.cancelled_appointment_ids.append(appointment_id)
            result.ambiguous_write_failures += 1
        return "ambiguous_write"
    outcome = _http_outcome(response.status_code, expected={200})
    if result is not None:
        if response.status_code == 200 or response.status_code >= 500:
            result.cancelled_appointment_ids.append(appointment_id)
        if response.status_code >= 500:
            result.ambiguous_write_failures += 1
    return outcome


async def _propose(
    session: _Session,
    fixture: WorkloadFixture,
    rng: random.Random,
    result: WorkloadResult | None = None,
    availability_session: _Session | None = None,
) -> str:
    try:
        staff_index = fixture.staff.index(session.credential)
    except ValueError:
        return "wrong_role"
    appointment_id = fixture.proposal_appointments[staff_index]
    history = await session.client.get(
        f"/api/v1/staff/me/appointments/{appointment_id}/change-proposals"
    )
    if history.status_code != 200:
        return _http_outcome(history.status_code, expected={200})
    context = history.json()["context"]
    selected_date = datetime.now(_BUSINESS_TIMEZONE).date() + timedelta(days=3)
    # Availability is student-only. The staff session performs history and mutation calls;
    # a distinct authenticated student session supplies a policy-valid slot preview.
    slot_session = availability_session or session
    availability = await slot_session.client.get(
        "/api/v1/availability",
        params={
            "purpose_id": str(fixture.purpose_id),
            "staff_id": str(session.credential.user_id),
            "date": selected_date.isoformat(),
        },
    )
    if availability.status_code != 200:
        return _http_outcome(availability.status_code, expected={200})
    slots = availability.json().get("slots", [])
    if not slots:
        return "no_available_slot"
    body = {
        "expected_proposal_version": context["proposal_version"],
        "expected_pending_proposal_id": context["pending_proposal_id"],
        "proposed_starts_at": slots[rng.randrange(len(slots))]["starts_at"],
        "proposed_location": "Fictional load lab room",
    }
    key = str(uuid4())
    route = f"/api/v1/staff/me/appointments/{appointment_id}/change-proposals"
    headers = {"Idempotency-Key": key}
    prior_attempt_ambiguous = False
    try:
        created = await session.client.post(route, json=body, headers=headers)
    except httpx.HTTPError:
        prior_attempt_ambiguous = True
        try:
            created = await session.client.post(route, json=body, headers=headers)
        except httpx.HTTPError:
            if result is not None:
                result.ambiguous_write_failures += 1
            return "ambiguous_write"
    if created.status_code == 409:
        if prior_attempt_ambiguous and result is not None:
            result.ambiguous_write_failures += 1
        return "business_conflict"
    if created.status_code not in {200, 201}:
        if created.status_code < 500:
            if prior_attempt_ambiguous and result is not None:
                result.ambiguous_write_failures += 1
            return _http_outcome(created.status_code, expected={200, 201})
        prior_attempt_ambiguous = True
        try:
            created = await session.client.post(route, json=body, headers=headers)
        except httpx.HTTPError:
            if result is not None:
                result.ambiguous_write_failures += 1
            return "ambiguous_write"
    receipt = _receipt(
        created,
        ("id", "appointment_id", "created_version", "created_at", "expires_at"),
    )
    if receipt is None:
        try:
            created = await session.client.post(route, json=body, headers=headers)
        except httpx.HTTPError:
            if result is not None:
                result.ambiguous_write_failures += 1
            return "ambiguous_write"
        receipt = _receipt(
            created,
            ("id", "appointment_id", "created_version", "created_at", "expires_at"),
        )
    if receipt is None:
        if result is not None:
            result.ambiguous_write_failures += 1
        return "ambiguous_write"
    if (
        receipt["appointment_id"] != str(appointment_id)
        or receipt["created_version"] != context["proposal_version"] + 1
    ):
        if result is not None:
            result.ambiguous_write_failures += 1
        return "ambiguous_write"
    proposal_id = UUID(str(receipt["id"]))
    if result is not None:
        result.created_proposal_ids.append(proposal_id)
    try:
        replay = await session.client.post(route, json=body, headers=headers)
    except httpx.HTTPError:
        replay = None
    if not _same_receipt(replay, receipt):
        if result is not None:
            result.idempotency_failures += 1
        return "idempotency_replay_failed"
    if result is not None:
        result.verified_replay_count += 1
    return "success"


def _http_outcome(status: int, *, expected: set[int]) -> str:
    if status in expected:
        return "success"
    if status == 409:
        return "business_conflict"
    if status == 429:
        return "capacity_rejection"
    if 400 <= status < 500:
        return "unexpected_client_error"
    if status >= 500:
        return "service_error"
    return "unexpected_http_status"


def _receipt(response: httpx.Response, keys: tuple[str, ...]) -> dict[str, object] | None:
    if response.status_code not in {200, 201}:
        return None
    try:
        value = response.json()
    except ValueError:
        return None
    if not isinstance(value, dict) or any(key not in value for key in keys):
        return None
    receipt = {key: value[key] for key in keys}
    try:
        UUID(str(receipt["id"]))
        if "appointment_id" in receipt:
            UUID(str(receipt["appointment_id"]))
    except ValueError:
        return None
    for key in ("created_at", "expires_at"):
        if key in receipt:
            timestamp = receipt[key]
            if not isinstance(timestamp, str):
                return None
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
    if "created_version" in receipt and (
        isinstance(receipt["created_version"], bool)
        or not isinstance(receipt["created_version"], int)
        or receipt["created_version"] < 0
    ):
        return None
    return receipt


def _same_receipt(response: httpx.Response | None, expected: dict[str, object]) -> bool:
    if response is None or response.status_code != 200:
        return False
    try:
        actual = response.json()
        return isinstance(actual, dict) and all(
            actual.get(key) == value for key, value in expected.items()
        )
    except (KeyError, TypeError, ValueError):
        return False


async def _monitor_event_loop(result: WorkloadResult, deadline: float) -> None:
    next_tick = time.monotonic() + 1
    while time.monotonic() < deadline:
        await asyncio.sleep(max(0, next_tick - time.monotonic()))
        now = time.monotonic()
        lag = max(0.0, now - next_tick)
        result.event_loop_lag_seconds.append(lag)
        next_tick = now + 1
