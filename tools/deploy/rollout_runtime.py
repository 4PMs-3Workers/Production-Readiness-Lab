"""Digest-only Compose execution; database volumes and migration history are retained."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.deploy.release import render
from tools.deploy.release_images import LOCAL_IMAGE_FORMAT, local_image_identity
from tools.deploy.release_manifest import MAX_JSON, Manifest, ReleaseError, load_json
from tools.deploy.rollout_schema import CATALOG_SQL, REVISION_SQL, SchemaSnapshot, snapshot
from tools.deploy.rollout_state import Reason, RolloutError


@dataclass(frozen=True)
class DatabaseIdentity:
    container_id: str
    volume: str


class Deadline:
    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.started = clock()
        self.expires = self.started + seconds

    def remaining(self, cap: float = 600) -> float:
        remaining = self.expires - self.clock()
        if remaining <= 0:
            raise RolloutError("deadline-exceeded")
        return min(cap, remaining)

    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)


class ReleaseRuntime:
    def __init__(
        self,
        base: list[str],
        environment: dict[str, str],
        origin: str,
        project: str,
        scratch: Path,
        *,
        overlay: str = "local",
        observability: bool = False,
    ):
        self.base, self.environment = list(base), dict(environment)
        self.origin, self.project, self.scratch = origin, project, scratch
        self.overlay, self.observability = overlay, observability
        self.deadline = Deadline(600)
        self.manifest: Manifest | None = None
        self.overlay_path: Path | None = None

    def command(
        self, args: list[str], *, reason: Reason = "rollout-failed", cap: float = 180
    ) -> bytes:
        try:
            result = subprocess.run(
                args,
                env=self.environment,
                capture_output=True,
                check=False,
                timeout=self.deadline.remaining(cap),
            )
            self.deadline.remaining()
            if result.returncode:
                raise RolloutError(reason)
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            self.deadline.remaining()
            raise RolloutError(reason) from None

    def compose(self, *args: str, reason: Reason = "rollout-failed", cap: float = 180) -> bytes:
        if self.overlay_path is None:
            raise RolloutError("invalid-manifest")
        return self.command(
            self.base + ["-f", str(self.overlay_path), *args], reason=reason, cap=cap
        )

    def select(self, manifest: Manifest) -> None:
        if (manifest.configuration.overlay, manifest.configuration.observability) != (
            self.overlay,
            self.observability,
        ):
            raise RolloutError("incompatible-configuration")
        raw = render(manifest)
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.scratch / (hashlib.sha256(raw).hexdigest() + ".yaml")
        if path.is_symlink() or (path.exists() and path.read_bytes() != raw):
            raise RolloutError("invalid-state")
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        self.manifest, self.overlay_path = manifest, path
        self.environment["SERVICE_RELEASE"] = manifest.provenance.source_sha

    def validate_plan(self) -> dict:
        if self.manifest is None:
            raise RolloutError("invalid-manifest")
        raw = self.compose(
            "--profile",
            "operations",
            "--profile",
            "seed",
            "config",
            "--no-env-resolution",
            "--format",
            "json",
            reason="incompatible-configuration",
        )
        try:
            config = load_json(raw)
            services = config["services"]
            if not isinstance(services, dict) or any(
                value.get("build") for value in services.values()
            ):
                raise ValueError()
            for name, key in self.manifest.services.items():
                if name == "backup":
                    continue
                if services[name]["image"] != self.manifest.images[key].reference:
                    raise ValueError()
            for name, image in self.manifest.vendor_images.items():
                if services[name]["image"] != image:
                    raise ValueError()
            if self.observability and services["backup_metrics_exporter"]["image"] != (
                self.manifest.images["backend"].reference
            ):
                raise ValueError()
            return config
        except (ValueError, KeyError, TypeError):
            raise RolloutError("incompatible-configuration") from None

    def migrate(self) -> None:
        # Native app.deploy preserves the migration/backup named lifecycle guard.
        self.compose(
            "--profile",
            "operations",
            "run",
            "--pull",
            "never",
            "--no-deps",
            "--rm",
            "-T",
            "migrate",
            reason="migration-failed",
            cap=125,
        )

    def rollout(self) -> None:
        self.compose("stop", "--timeout", "40", "api", "worker", "frontend", cap=140)
        self.compose("run", "--pull", "never", "--no-deps", "--rm", "-T", "schema", cap=15)
        self.compose(
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--no-deps",
            "--force-recreate",
            "api",
            "worker",
            "frontend",
            cap=100,
        )
        # Recreate the reviewed gateway to refresh service DNS, without recreating MySQL.
        self.compose(
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--no-deps",
            "--force-recreate",
            "gateway",
            cap=40,
        )

    def json_output(self, raw: bytes) -> dict:
        try:
            if len(raw) > MAX_JSON:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError, UnicodeError):
            raise RolloutError("invalid-state") from None

    def inspect(self, service: str, *, optional: bool = False) -> dict | None:
        identifier = self.compose("ps", "--all", "-q", service).decode().strip()
        if not identifier and optional:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise RolloutError("image-mismatch")
        raw = self.command(
            [
                "docker",
                "inspect",
                "--format",
                '{"id":{{json .Id}},"image":{{json .Image}},'
                '"reference":{{json .Config.Image}},"labels":{{json .Config.Labels}},'
                '"running":{{json .State.Running}},"mounts":{{json .Mounts}},'
                '"health":{{with (index .State "Health")}}{{json .Status}}{{else}}null{{end}}}',
                identifier,
            ]
        )
        record = self.json_output(raw)
        if (
            record.get("id") != identifier
            or (record.get("labels") or {}).get("com.docker.compose.project") != self.project
            or (record.get("labels") or {}).get("com.docker.compose.service") != service
        ):
            raise RolloutError("image-mismatch")
        return record

    def verify_images(self) -> None:
        if self.manifest is None:
            raise RolloutError("invalid-manifest")
        for service in ("api", "worker", "frontend"):
            key = "frontend" if service == "frontend" else "backend"
            record = self.inspect(service)
            image = self.manifest.images[key]
            if (
                record is None
                or record.get("reference") != image.reference
                or record.get("running") is not True
                or (record.get("labels") or {}).get("org.opencontainers.image.revision")
                != self.manifest.provenance.source_sha
            ):
                raise RolloutError("image-mismatch")
            if record["image"] != image.image_id:
                local = self.json_output(
                    self.command(
                        [
                            "docker",
                            "image",
                            "inspect",
                            "--format",
                            LOCAL_IMAGE_FORMAT,
                            image.reference,
                        ],
                        reason="image-mismatch",
                    )
                )
                try:
                    if record["image"] != local_image_identity(image, local):
                        raise RolloutError("image-mismatch")
                except ReleaseError:
                    raise RolloutError("image-mismatch") from None

    def vendor_images(self) -> None:
        if self.manifest is None:
            raise RolloutError("invalid-manifest")
        for reference in self.manifest.vendor_images.values():
            self.command(
                ["docker", "pull", "--platform", "linux/amd64", reference], reason="missing-images"
            )

    def database_identity(self) -> DatabaseIdentity:
        record = self.inspect("mysql")
        if record is None or record.get("running") is not True or self.manifest is None:
            raise RolloutError("database-unavailable")
        expected = (
            self.command(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    self.manifest.vendor_images["mysql"],
                ],
                reason="image-mismatch",
            )
            .decode()
            .strip()
        )
        if record.get("image") != expected:
            raise RolloutError("image-mismatch")
        mounts = [
            entry
            for entry in record.get("mounts", [])
            if entry.get("Destination") == "/var/lib/mysql"
        ]
        volume = f"{self.project}_mysql_data"
        if len(mounts) != 1 or mounts[0].get("Type") != "volume" or mounts[0].get("Name") != volume:
            raise RolloutError("volume-changed")
        labels = self.json_output(
            self.command(
                ["docker", "volume", "inspect", "--format", "{{json .Labels}}", volume],
                reason="volume-changed",
            )
        )
        if labels.get("com.docker.compose.project") != self.project:
            raise RolloutError("volume-changed")
        return DatabaseIdentity(record["id"], volume)

    def require_database(self, before: DatabaseIdentity) -> None:
        if self.database_identity() != before:
            raise RolloutError("volume-changed")

    def sql(self, statement: str) -> bytes:
        return self.compose(
            "exec",
            "-T",
            "mysql",
            "bash",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot '
            '--connect-timeout=3 --batch --skip-column-names -e "$1"',
            "--",
            statement,
            reason="database-unavailable",
            cap=15,
        ).strip()

    def schema(self) -> SchemaSnapshot:
        record = self.inspect("mysql", optional=True)
        if record is None:
            return SchemaSnapshot(
                tables={}, revisions=[], unsupported_objects=0, nontransactional_tables=0
            )
        self.database_identity()
        catalog = self.json_output(self.sql(CATALOG_SQL))
        revisions = []
        if any(row.get("table") == "alembic_version" for row in catalog.get("columns", [])):
            try:
                revisions = json.loads(self.sql(REVISION_SQL))
                if not isinstance(revisions, list):
                    raise ValueError()
            except (ValueError, TypeError):
                raise RolloutError("schema-partial") from None
        return snapshot(catalog, revisions)

    def prepare_database(self) -> DatabaseIdentity:
        if self.inspect("mysql", optional=True) is None:
            self.compose(
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "120",
                "mysql",
                reason="database-unavailable",
                cap=130,
            )
        return self.database_identity()

    def apply_grants(self) -> None:
        self.compose(
            "exec",
            "-T",
            "mysql",
            "bash",
            "/opt/prl/apply-grants.sh",
            reason="migration-failed",
            cap=15,
        )

    def appointment_status(self, appointment: str) -> str:
        from uuid import UUID

        identifier = UUID(appointment).hex
        try:
            value = self.sql(
                f"SELECT status FROM prl.appointments WHERE id=UNHEX('{identifier}');"
            ).decode("ascii")
            if value not in {"UPCOMING", "CHANGE_PENDING", "CANCELLED", "COMPLETED", "NO_SHOW"}:
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise RolloutError("smoke-failed") from None

    def effects(self, appointment: str) -> tuple[int, int, int]:
        from uuid import UUID

        identifier = str(UUID(appointment))
        script = """
import asyncio,json,os,sys
from uuid import UUID
from sqlalchemy import text
from app.core.database import create_database_engine
async def main():
    key=UUID(sys.argv[1])
    engine=create_database_engine(os.environ['DATABASE_URL'],hide_parameters=True)
    try:
        async with engine.connect() as db:
            row=(await db.execute(text("SELECT "
                "(SELECT COUNT(*) FROM appointments WHERE id=:binary),"
                "(SELECT COUNT(*) FROM audit_events WHERE entity_id=:identifier),"
                "(SELECT COUNT(*) FROM notifications WHERE appointment_id=:binary)"),
                dict(binary=key.bytes,identifier=str(key)))).one()
            print(json.dumps(list(row)))
    finally:
        await engine.dispose()
asyncio.run(main())
"""
        raw = self.compose(
            "run",
            "--pull",
            "never",
            "--no-deps",
            "--rm",
            "-T",
            "schema",
            "python",
            "-c",
            script,
            identifier,
            reason="smoke-failed",
            cap=15,
        )
        try:
            values = json.loads(raw)
            if (
                not isinstance(values, list)
                or len(values) != 3
                or any(type(v) is not int or v < 0 for v in values)
            ):
                raise ValueError()
            return values[0], values[1], values[2]
        except (ValueError, TypeError):
            raise RolloutError("smoke-failed") from None
