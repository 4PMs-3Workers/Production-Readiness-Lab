"""Fictional, UUID-owned recovery rehearsal using the reviewed #34 environment.

Only sanitized JSON reports leave this harness. Payloads, credentials, cookies,
account identifiers and application responses stay inside private scratch storage.
"""

import ipaddress
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from tests.containers.test_compose_acceptance import (
    DEPLOY,
    ROOT,
    AcceptanceFailure,
    ComposeAcceptance,
    invoke,
    json_output,
    one_volume,
    require_success,
)
from tools.backup.evidence import write_report
from tools.backup.recovery_config import render

from app.schemas.appointments import AppointmentCancelResponse

FINGERPRINT = """
import asyncio, hashlib, json, os, time
from contextlib import aclosing
from app.core.database import create_database_engine
from app.backup.schema import read_schema
from app.backup.snapshot import consistent_snapshot, row_records
async def main():
    engine = create_database_engine(os.environ['DATABASE_URL'])
    try:
        async with engine.connect() as db:
            schema = await read_schema(db)
            await db.rollback()
            result = {}
            async with consistent_snapshot(db):
                for table in schema.tables:
                    digest, count = hashlib.sha256(), 0
                    async with aclosing(row_records(db, table, 30)) as rows:
                        async for record in rows:
                            digest.update(record)
                            count += 1
                    result[table.name] = [count, digest.hexdigest()]
            print(json.dumps(result, sort_keys=True))
    finally:
        await engine.dispose()
asyncio.run(main())
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stage(name: str) -> None:
    print(json.dumps(dict(rehearsal_stage=name)), flush=True)


class RecoveryStack(ComposeAcceptance):
    def __init__(self, scratch: Path, *, recovered: bool = False, http_port: int | None = None):
        self.recovered = recovered
        self.database = "prl"
        self.deployment_root = DEPLOY
        scratch.mkdir(mode=0o700, parents=True)
        super().__init__(scratch, http_port=http_port)
        self.owned_image_tag = self.image_tag
        for path in self.env_files.values():
            Path(path).chmod(0o600)
        self.base.extend(["-f", str(DEPLOY / "compose.backup.yaml")])
        if recovered:
            self.env.update(render(scratch / "nginx"))
            self.base.extend(["-f", str(DEPLOY / "compose.recovery.yaml")])
        self.backup_password = secrets.token_urlsafe(32)
        with Path(self.env_files["PRL_DB_ENV_FILE"]).open("a", encoding="utf-8") as file:
            file.write(f"PRL_BACKUP_PASSWORD={self.backup_password}\n")
        self.backup_env = scratch / "backup.env"
        self.env.update(
            PRL_BACKUP_ENV_FILE=str(self.backup_env.resolve()),
            PRL_SOURCE_SHA=self.expected_sha,
            PRL_BACKEND_IMAGE_ID="sha256:" + "0" * 64,
        )

    def _ensure_gateway_subnet_available(self, *, additional_subnets=()) -> None:
        if not self.recovered:
            return super()._ensure_gateway_subnet_available(additional_subnets=additional_subnets)
        reserved = ipaddress.ip_network("172.30.36.0/29")
        ids = require_success(invoke(["docker", "network", "ls", "-q"]), "Network inventory failed")
        for identifier in ids.splitlines():
            records = json_output(
                invoke(["docker", "network", "inspect", identifier]), "Network check failed"
            )
            for entry in (records[0].get("IPAM") or {}).get("Config") or []:
                subnet = entry.get("Subnet")
                if subnet and ipaddress.ip_network(subnet).version == 4:
                    if reserved.overlaps(ipaddress.ip_network(subnet)):
                        raise AcceptanceFailure("The separate recovery subnet is already occupied")

    def cleanup(self) -> None:
        shared = self.image_tag
        self.image_tag = self.owned_image_tag
        try:
            super().cleanup()
        finally:
            self.image_tag = shared

    def operations_config(self, *, target: str | None = None) -> None:
        values = dict(
            DATABASE_URL=(
                f"mysql+asyncmy://prl_backup:{quote(self.backup_password, safe='')}@mysql/prl"
            ),
            BACKUP_MAX_SECONDS="600",
            BACKUP_QUERY_SECONDS="30",
            BACKUP_KEEP_COUNT="2",
        )
        if target:
            values["RESTORE_DATABASE_URL"] = (
                "mysql+asyncmy://prl_migration:"
                f"{quote(self.db_passwords['migration'], safe='')}@mysql/{target}"
            )
        self.backup_env.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
        )
        self.backup_env.chmod(0o600)

    def operation(self, *args: str) -> dict:
        output = require_success(
            self.compose(
                "--profile",
                "backup",
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "backup",
                *args,
                timeout=660,
            ),
            "A recovery command failed (payload and raw errors withheld)",
        )
        report = json.loads(output.splitlines()[-1])
        if report.get("success") is not True:
            raise AcceptanceFailure("A recovery command did not report verified success")
        return report

    def fingerprint(self) -> dict:
        return json_output(
            self.compose(
                "run", "--rm", "--no-deps", "-T", "schema", "python", "-c", FINGERPRINT, timeout=90
            ),
            "A private source-state fingerprint failed",
        )

    def effects(self, appointment: str) -> tuple[int, int, int]:
        if not re.fullmatch(r"[0-9a-f-]{36}", appointment):
            raise AcceptanceFailure("Invalid private smoke identifier")
        database = self.database
        binary_id = f"UNHEX(REPLACE('{appointment}','-',''))"
        sql = (
            f"SELECT (SELECT COUNT(*) FROM `{database}`.appointments WHERE id={binary_id}),"
            f"(SELECT COUNT(*) FROM `{database}`.audit_events WHERE entity_id='{appointment}'),"
            f"(SELECT COUNT(*) FROM `{database}`.notifications WHERE appointment_id={binary_id});"
        )
        appointment_count, audit_count, notification_count = map(int, self.sql(sql).split())
        return appointment_count, audit_count, notification_count

    def appointment_status(self, appointment: str) -> str:
        identifier = str(UUID(appointment))
        return self.sql(
            f"SELECT status FROM `{self.database}`.appointments "
            f"WHERE id=UNHEX(REPLACE('{identifier}','-',''));"
        )


def request(client: httpx.Client, method: str, path: str, expected: int, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        # Never expose response bodies, IDs, URLs, headers or cookies in evidence.
        raise AcceptanceFailure(
            f"Application smoke failed: {method} expected {expected}, "
            f"received {response.status_code}"
        )
    return response


def booking_payload(client: httpx.Client) -> dict:
    context = request(client, "GET", "/api/v1/booking-context", 200).json()
    purposes = request(client, "GET", "/api/v1/purposes?page=1&page_size=50", 200).json()["items"]
    purpose = next(item for item in purposes if item["code"] == "CAREER_INTERNSHIP")
    staff_items = request(
        client,
        "GET",
        "/api/v1/staff",
        200,
        params=dict(purpose_id=purpose["id"], page=1, page_size=50),
    ).json()["items"]
    staff = next(item for item in staff_items if item["name"] == "Lê Thu Hà")
    selected = max(
        date.fromisoformat(context["min_booking_date"]),
        date.fromisoformat(context["business_date"]) + timedelta(days=1),
    )
    while selected <= date.fromisoformat(context["max_booking_date"]):
        slots = request(
            client,
            "GET",
            "/api/v1/availability",
            200,
            params=dict(purpose_id=purpose["id"], staff_id=staff["id"], date=selected.isoformat()),
        ).json()["slots"]
        if slots:
            return dict(
                purpose_id=purpose["id"], staff_id=staff["id"], starts_at=slots[0]["starts_at"]
            )
        selected += timedelta(days=1)
    raise AcceptanceFailure("No future cancellable fictional appointment slot exists")


def book(stack: RecoveryStack) -> str:
    with stack.api_client() as client:
        csrf = stack.login(client, stack.student_email, stack.student_password)
        response = request(
            client,
            "POST",
            "/api/v1/appointments",
            201,
            headers={"Origin": stack.origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex},
            json=booking_payload(client),
        )
        return str(response.json()["id"])


def propose(stack: RecoveryStack, appointment: str) -> None:
    with stack.api_client() as student:
        stack.login(student, stack.student_email, stack.student_password)
        slot = booking_payload(student)
    with stack.api_client() as staff:
        csrf = stack.login(staff, stack.staff_email, stack.staff_password)
        request(
            staff,
            "POST",
            f"/api/v1/staff/me/appointments/{appointment}/change-proposals",
            201,
            headers={"Origin": stack.origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex},
            json=dict(
                expected_proposal_version=0,
                expected_pending_proposal_id=None,
                proposed_starts_at=slot["starts_at"],
                proposed_location=None,
            ),
        )


def application_smoke(stack: RecoveryStack, persisted: str, later: str) -> dict:
    with stack.api_client() as client:
        csrf = stack.login(client, stack.student_email, stack.student_password)
        headers = {"Origin": stack.origin, "X-CSRF-Token": csrf}
        request(client, "GET", "/login", 200)
        history = request(client, "GET", "/api/v1/appointments?page=1&page_size=50", 200).json()[
            "items"
        ]
        if persisted not in {item["id"] for item in history} or later in {
            item["id"] for item in history
        }:
            raise AcceptanceFailure("Recovered history does not reflect the recorded snapshot")
        request(client, "GET", f"/api/v1/appointments/{persisted}", 200)
        request(client, "GET", f"/api/v1/appointments/{later}", 404)
        payload, key = booking_payload(client), uuid4().hex
        receipt = request(
            client,
            "POST",
            "/api/v1/appointments",
            201,
            headers={**headers, "Idempotency-Key": key},
            json=payload,
        ).json()
        appointment = receipt["id"]
        if stack.effects(appointment) != (1, 1, 2):
            raise AcceptanceFailure(
                "Booking did not produce exactly one appointment/audit and two notifications"
            )
        replay = request(
            client,
            "POST",
            "/api/v1/appointments",
            200,
            headers={**headers, "Idempotency-Key": key},
            json=payload,
        ).json()
        if replay != receipt or stack.effects(appointment) != (1, 1, 2):
            raise AcceptanceFailure("Booking replay duplicated effects or changed the receipt")
        cancelled = request(
            client,
            "POST",
            f"/api/v1/appointments/{appointment}/cancel",
            200,
            headers=headers,
            json={},
        ).json()
        replay_cancel = request(
            client,
            "POST",
            f"/api/v1/appointments/{appointment}/cancel",
            200,
            headers=headers,
            json={},
        ).json()
        if cancelled != replay_cancel or stack.effects(appointment) != (1, 2, 4):
            raise AcceptanceFailure("Cancellation replay duplicated effects or changed the result")
        receipt = AppointmentCancelResponse.model_validate_json(json.dumps(cancelled), strict=True)
        detail = request(client, "GET", f"/api/v1/appointments/{appointment}", 200).json()
        if (
            str(receipt.id) != appointment
            or detail.get("id") != appointment
            or detail.get("status") != "CANCELLED"
            or datetime.fromisoformat(detail["cancelled_at"].replace("Z", "+00:00"))
            != receipt.cancelled_at
            or stack.appointment_status(appointment) != "CANCELLED"
        ):
            raise AcceptanceFailure("Cancellation was not independently persisted")
        request(client, "POST", "/api/v1/auth/logout", 204, headers=headers)
        request(client, "GET", "/api/v1/appointments", 401)
    return {
        name: True
        for name in (
            "frontend",
            "login",
            "history",
            "detail",
            "availability",
            "book",
            "book_replay",
            "cancel",
            "cancel_replay",
            "logout",
            "side_effects_exact",
            "later_change_excluded",
        )
    }


def images(stack: RecoveryStack) -> dict:
    result = {}
    for service in ("api", "worker", "frontend", "gateway", "mysql"):
        identifier = require_success(
            stack.compose("ps", "-q", service), "A rehearsal service is missing"
        )
        record = json_output(
            invoke(["docker", "inspect", identifier]), "Image identity check failed"
        )[0]
        result[service] = record["Image"]
        if service in {"api", "worker", "frontend"}:
            if (record["Config"].get("Labels") or {}).get(
                "org.opencontainers.image.revision"
            ) != stack.expected_sha:
                raise AcceptanceFailure("A rehearsal service uses an incompatible source revision")
    return result


def start_recovered(stack: RecoveryStack, *services: str, failure: str) -> None:
    result = stack.compose("up", "-d", "--wait", "--wait-timeout", "180", *services, timeout=240)
    if result.returncode != 0:
        # Command and container output can include credentials or fixture data.
        # Emit only fixed diagnostic categories and Docker's enumerated states.
        patterns = {
            "bind": ("invalid mount", "bind source path", "not a directory"),
            "network": ("failed to create network", "network sandbox", "endpoint"),
            "network_address": ("pool overlaps", "address already in use", "address not available"),
            "network_firewall": ("iptables", "ip6tables", "nftables"),
            "network_bridge": ("bridge", "veth", "ip_forward"),
            "runtime": ("failed to create task", "failed to create shim", "oci runtime"),
            "runtime_sysctl": ("sysctl", "reopen fd"),
            "permission": ("permission denied", "operation not permitted"),
            "apparmor": ("apparmor",),
            "storage": ("no space left", "read-only file system", "disk full"),
            "configuration": ("no such file", "invalid interpolation", "unknown flag", "not found"),
            "image": ("no such image", "pull access denied", "pull policy"),
            "unhealthy": ("unhealthy", "waiting for container"),
            "bootstrap": ("initialization of server has completed", "initializing database"),
            "bootstrap_complete": ("init process done",),
            "bootstrap_script_sourced": ("sourcing /docker-entrypoint-initdb.d/10-identities.sh",),
            "bootstrap_script_executed": ("running /docker-entrypoint-initdb.d/10-identities.sh",),
            "shell_directory": ("is a directory",),
            "shell_syntax": ("syntax error", "unexpected token", "bad interpreter"),
            "shell_lookup": (
                "command not found",
                "unbound variable",
                "missing migration password",
                "missing runtime password",
                "missing seed password",
            ),
            "mysql_connection": ("can't connect", "lost connection", "unknown mysql server host"),
            "mysql_process": ("segmentation fault", "illegal instruction", "killed"),
            "ready": ("ready for connections",),
            "authentication": ("access denied",),
            "mysql_runtime_probe": ("mysql-runtime-probe-failed",),
        }
        messages = [result.stdout, result.stderr]
        states = {}
        try:
            logs = stack.compose("logs", "--tail", "40", *services, timeout=15)
            messages.extend((logs.stdout, logs.stderr))
            for service in services:
                listed = stack.compose("ps", "--all", "-q", service, timeout=10)
                identifier = listed.stdout.strip()
                if listed.returncode or not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
                    continue
                inspected = invoke(
                    ["docker", "inspect", "--format", "{{json .State}}", identifier], timeout=10
                )
                if inspected.returncode:
                    continue
                state = json.loads(inspected.stdout)
                status = state.get("Status")
                health_state = state.get("Health") or {}
                health = health_state.get("Status")
                messages.extend(
                    item["Output"]
                    for item in (health_state.get("Log") or [])
                    if isinstance(item.get("Output"), str)
                )
                if isinstance(state.get("Error"), str):
                    messages.append(state["Error"])
                states[service] = {
                    "status": (
                        status
                        if status in {"created", "running", "restarting", "exited", "dead"}
                        else "unknown"
                    ),
                    "health": health if health in {"starting", "healthy", "unhealthy"} else None,
                    "exit_code": (
                        state.get("ExitCode") if type(state.get("ExitCode")) is int else None
                    ),
                }
        except (AcceptanceFailure, json.JSONDecodeError):
            pass
        output = "\n".join(messages).casefold()
        flags = [name for name, matches in patterns.items() if any(m in output for m in matches)]
        mysql_errors = sorted(set(re.findall(r"\berror ([1-5][0-9]{3}) \([0-9a-z]{5}\)", output)))
        socket_errors = sorted(
            set(re.findall(r"\berror 2002 \(hy000\):[^\n]*\(([0-9]{1,3})\)\s*$", output, re.M))
        )
        print(
            json.dumps(
                dict(
                    rehearsal_stage="recovery_startup_failed",
                    signals=flags,
                    states=states,
                    mysql_error_codes=mysql_errors,
                    socket_error_codes=socket_errors,
                )
            ),
            flush=True,
        )
    require_success(result, failure)


class RecoveryTransfer(Protocol):
    recovery_volume: str

    def publish(self, source: RecoveryStack, backup: dict, source_images: dict) -> None: ...
    def freeze_source(self, source: RecoveryStack) -> None: ...
    def isolate(self, source: RecoveryStack) -> None: ...
    def recovery_engine(self) -> AbstractContextManager[tuple[Path, int]]: ...

    def retrieve(self, target: RecoveryStack, backup_id: str) -> None: ...

    def validate_images(self, source: dict, target: dict) -> dict: ...
    def resume_source_for_verification(self, source: RecoveryStack) -> None: ...


def run(
    scratch: Path,
    *,
    stack_factory: Callable[..., RecoveryStack] = RecoveryStack,
    transport: RecoveryTransfer | None = None,
) -> dict:
    if require_success(invoke(["git", "status", "--porcelain"]), "Clean checkout check failed"):
        raise AcceptanceFailure("A clean committed checkout is required for source provenance")
    scratch.mkdir(parents=True, mode=0o700, exist_ok=True)
    source, recovered = None, None
    storage = "prl-recovery-store-" + uuid4().hex
    owner = uuid4().hex
    created = False
    try:
        stage("prepare_source")
        source = stack_factory(scratch / "source")
        source.database = "prl"
        source.env["PRL_BACKUP_VOLUME"] = storage
        source.operations_config()
        require_success(
            invoke(
                [
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    f"prl.rehearsal={owner}",
                    "--label",
                    "prl.backup.storage=1",
                    storage,
                ]
            ),
            "Private backup volume creation failed",
        )
        created = True
        stage("build_and_start_reviewed_source")
        source.start()
        # Existing-volume onboarding must also work without bootstrap reexecution.
        provision = (DEPLOY / "mysql/provision-backup.sh").read_text(encoding="utf-8")
        for _ in range(2):
            require_success(
                source.compose(
                    "exec", "-T", "mysql", "bash", "-s", input_text=provision, timeout=45
                ),
                "Explicit least-privilege backup identity provisioning failed",
            )
        source_images = images(source)
        source.env["PRL_BACKEND_IMAGE_ID"] = source_images["api"]
        persisted = book(source)
        propose(source, persisted)
        stage("consistent_online_backup")
        backup = source.operation("backup")
        if (
            backup["source_sha"] != source.expected_sha
            or backup["image_id"] != source_images["api"]
        ):
            raise AcceptanceFailure("Backup release identity differs from the running source")
        if transport is not None:
            transport.publish(source, backup, source_images)
        later = book(source)
        incident = source.sql("SELECT DATE_FORMAT(UTC_TIMESTAMP(6), '%Y-%m-%dT%H:%i:%s.%fZ');")
        if transport is not None:
            transport.freeze_source(source)
        source_fingerprint = source.fingerprint()
        if transport is not None:
            transport.isolate(source)
        stage("provision_separate_recovery")
        recovery_start, clock_start = now(), time.monotonic()
        engine_images = None
        engine = (
            transport.recovery_engine()
            if transport is not None
            else nullcontext((scratch / "recovered", None))
        )
        with engine as (recovery_path, http_port):
            try:
                recovered = stack_factory(recovery_path, recovered=True, http_port=http_port)
                recovered.database = "prl_restore_" + uuid4().hex[:20]
                recovered.image_tag = source.image_tag
                recovered.student_password = source.student_password
                recovered.env.update(
                    PRL_IMAGE_TAG=source.image_tag,
                    PRL_BACKUP_VOLUME=(
                        transport.recovery_volume if transport is not None else storage
                    ),
                    PRL_BACKEND_IMAGE_ID=source_images["api"],
                )
                runtime_file = Path(recovered.env_files["PRL_RUNTIME_ENV_FILE"])
                runtime = runtime_file.read_text(encoding="utf-8").replace(
                    "@mysql/prl\n", f"@mysql/{recovered.database}\n"
                )
                runtime_file.write_text(runtime, encoding="utf-8")
                recovered.operations_config(target=recovered.database)
                if transport is not None:
                    transport.retrieve(recovered, backup["backup_id"])
                start_recovered(
                    recovered, "mysql", failure="Isolated recovery database startup failed"
                )
                grant_mode = recovered.sql("SELECT @@global.partial_revokes;")
                if grant_mode not in {"0", "1"}:
                    raise AcceptanceFailure("Recovery database grant mode could not be established")
                grant_name = (
                    recovered.database.replace("_", r"\_")
                    if grant_mode == "0"
                    else recovered.database
                )
                recovered.sql(
                    f"CREATE DATABASE `{recovered.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
                    "GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,"
                    "INDEX,REFERENCES,TRIGGER,EVENT "
                    f"ON `{grant_name}`.* TO 'prl_migration'@'%';"
                    "GRANT SHOW_ROUTINE ON *.* TO 'prl_migration'@'%';"
                )
                actual_grants = recovered.sql("SHOW GRANTS FOR 'prl_migration'@'%';")
                if f" ON `{grant_name}`.* TO " not in actual_grants or (
                    grant_mode == "0" and f" ON `{recovered.database}`.* TO " in actual_grants
                ):
                    raise AcceptanceFailure(
                        "Recovery requires the literal database grant without a wildcard workaround"
                    )
                stage("restore_and_verify")
                validation = recovered.operation(
                    "restore",
                    "--backup-id",
                    backup["backup_id"],
                    "--target-database",
                    recovered.database,
                )
                verified = recovered.operation(
                    "verify",
                    "--backup-id",
                    backup["backup_id"],
                    "--target-database",
                    recovered.database,
                )
                if validation["table_counts"] != verified["table_counts"]:
                    raise AcceptanceFailure(
                        "Read-only verification disagrees with the restore report"
                    )
                grants = (
                    (getattr(recovered, "deployment_root", DEPLOY) / "mysql/grants.sql")
                    .read_text(encoding="utf-8")
                    .replace("prl.", f"`{recovered.database}`.")
                )
                recovered.sql(grants)
                stage("start_compatible_recovered_application")
                start_recovered(
                    recovered,
                    "api",
                    "worker",
                    "frontend",
                    "gateway",
                    failure="Recovered application startup failed",
                )
                recovered.wait_gateway()
                recovered.require_worker_identity()
                recovered.wait_heartbeat()
                recovered_images = images(recovered)
                if transport is not None:
                    engine_images = transport.validate_images(source_images, recovered_images)
                elif source_images != recovered_images:
                    raise AcceptanceFailure(
                        "Recovered service images differ from the reviewed source"
                    )
                stage("real_recovered_application_smoke")
                smoke = application_smoke(recovered, persisted, later)
                smoke_passed, duration = now(), time.monotonic() - clock_start
            finally:
                if recovered is not None:
                    recovered.cleanup()
        if transport is not None:
            transport.resume_source_for_verification(source)
        if source.fingerprint() != source_fingerprint:
            raise AcceptanceFailure("Source data changed during isolated recovery and smoke")
        status = source.operation("status")
        if status["last_successful_backup"] != backup["backup_id"]:
            raise AcceptanceFailure("Backup status does not identify the completed artifact")
        before = datetime.fromisoformat(backup["snapshot"]["established_before"])
        after = datetime.fromisoformat(backup["snapshot"]["established_after"])
        incident_time = datetime.fromisoformat(incident)
        report = dict(
            evidence_version=1,
            accepted=True,
            source_sha=source.expected_sha,
            pr_head_sha=os.environ.get("PR_HEAD_SHA"),
            source_images=source_images,
            recovered_images=recovered_images,
            backup=backup,
            validation={
                key: validation[key]
                for key in (
                    "row_hashes_verified",
                    "schema_verified",
                    "foreign_keys_checked",
                    "generated_columns_checked",
                    "utc_verified",
                    "table_counts",
                    "schema_sha256",
                )
            },
            smoke=smoke,
            source_unchanged=True,
            target_migrated=False,
            target_seeded=False,
            timing=dict(
                incident_at=incident,
                recovery_started_at=recovery_start,
                smoke_passed_at=smoke_passed,
                rpo_observed_min_seconds=(incident_time - after).total_seconds(),
                rpo_observed_max_seconds=(incident_time - before).total_seconds(),
                rto_service_seconds=duration,
            ),
            commands=[
                "compose build api frontend (source SHA labeled)",
                "source: migrate; apply-grants; seed (fictional only)",
                "app.backup backup",
                "source: later identifiable booking; record incident",
                "create separate empty recovery database",
                "app.backup restore --backup-id <id> --target-database <isolated>",
                "app.backup verify --backup-id <id> --target-database <isolated>",
                "target: apply reviewed runtime grants; start api worker frontend gateway",
                "target: login/history/detail/availability/book/replay/cancel/replay/logout",
                "compare source fingerprints",
            ],
        )
        if engine_images is not None:
            report["engine_images"] = engine_images
        return report
    finally:
        stage("cleanup_owned_rehearsal_resources")
        try:
            if source is not None:
                source.cleanup()
        finally:
            if created:
                one_volume(storage, {"prl.rehearsal": owner, "prl.backup.storage": "1"})
                require_success(
                    invoke(["docker", "volume", "rm", storage]),
                    "Owned rehearsal backup volume cleanup failed",
                )


def main() -> int:
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="prl-recovery-") as directory:
            report = run(Path(directory))
        destination = ROOT / "build/recovery-review/rehearsal.json"
        write_report(report, destination)
        stage("accepted_report_saved")
        return 0
    except AcceptanceFailure as error:
        print(json.dumps(dict(accepted=False, reason=str(error))))
        return 1
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, StopIteration):
        print(json.dumps(dict(accepted=False, reason="REHEARSAL_FAILED_PRIVATE_DETAILS_WITHHELD")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
