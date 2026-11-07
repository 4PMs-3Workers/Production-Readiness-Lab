"""Exercise the reviewed restore workflow while source services/storage are unavailable."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tests.rollout.fixtures import Artifacts
from tools.backup.offhost import Report
from tools.backup.offhost_contract import (
    Code,
    Completion,
    RemoteError,
    canonical,
    contract,
    source_identity,
)
from tools.backup.offhost_docker_lab import ENGINE, containerd_store, empty_recovery_daemon
from tools.backup.offhost_escrow import (
    Escrow,
    capture,
    image_overlay,
)
from tools.backup.offhost_io import Deadline, directory
from tools.backup.offhost_io import run as command
from tools.backup.offhost_lab import StoreLab, age_keys
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import read_point
from tools.backup.rehearsal import DEPLOY, ROOT, AcceptanceFailure, RecoveryStack, run, stage
from tools.deploy.release import render
from tools.deploy.release_manifest import Manifest, write

from app.backup.config import BackupConfig
from app.backup.manifest import BACKUP_ID
from app.backup.storage import load_artifact, write_private


def cli_report(args: list[str], operation: str, deadline: Deadline) -> Report:
    """Preserve only the CLI's validated finite reason when an operation fails."""
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=deadline.remaining(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RemoteError(Code.TIMEOUT) from None
        except OSError:
            raise RemoteError(Code.CONFIGURATION) from None
        if output.tell() > 16384:
            raise RemoteError(Code.INTEGRITY)
        output.seek(0)
        report = contract(output.read(16385), Report)
    if (
        report.operation != operation
        or report.success != (completed.returncode == 0)
        or report.application_rehearsal_verified
    ):
        raise RemoteError(Code.INTEGRITY)
    if not report.success:
        raise RemoteError(report.reason or Code.INTEGRITY)
    return report


class ImmutableRecoveryStack(RecoveryStack):
    def __init__(self, scratch: Path, manifest: Manifest, *, recovered: bool = False):
        super().__init__(scratch, recovered=recovered)
        self.manifest = manifest
        self.release_overlay = scratch / "release.yaml"
        self.release_overlay.write_bytes(render(manifest, backup=True))
        self.base.extend(["-f", str(self.release_overlay)])

    def start(self) -> None:
        from tests.containers.test_compose_acceptance import require_success

        steps = [
            (
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "180",
                "mysql",
            ),
            (
                "--profile",
                "operations",
                "run",
                "--rm",
                "--pull",
                "never",
                "--no-deps",
                "-T",
                "migrate",
            ),
            ("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh"),
            ("--profile", "seed", "run", "--rm", "--pull", "never", "--no-deps", "-T", "seed"),
            ("run", "--rm", "--pull", "never", "--no-deps", "-T", "schema"),
            (
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "180",
                "api",
                "worker",
                "frontend",
                "gateway",
            ),
        ]
        for args in steps:
            require_success(
                self.compose(*args, timeout=240), "Immutable source fixture setup failed"
            )
        self.started = True
        self.wait_gateway()
        self.require_worker_identity()
        self.wait_heartbeat()


def verify_mysql_bootstrap_mount(stack: RecoveryStack) -> None:
    expected = hashlib.sha256(
        (stack.deployment_root / "mysql/10-identities.sh").read_bytes()
    ).hexdigest()
    inspected = stack.compose(
        "run",
        "--rm",
        "--no-deps",
        "--pull",
        "never",
        "--user",
        "mysql",
        "--entrypoint",
        "/bin/sh",
        "-T",
        "mysql",
        "-ec",
        'test -f "$1" && test -r "$1" && sha256sum "$1"',
        "sh",
        "/docker-entrypoint-initdb.d/10-identities.sh",
        timeout=30,
    )
    if inspected.returncode or inspected.stdout.split()[:1] != [expected]:
        raise AcceptanceFailure("Recovered MySQL cannot read the verified bootstrap file")


class RemoteRecovery:
    def __init__(
        self, scratch: Path, lab: StoreLab, age: Path, artifacts: Artifacts, manifest: Manifest
    ):
        self.scratch, self.lab, self.age = directory(scratch), lab, age
        self.artifacts, self.manifest = artifacts, manifest
        self.owner = uuid4().hex
        self.recovery_volume = "prl-offhost-recovery-" + self.owner
        self.source_isolated = self.target_isolated = False
        self.local_store = scratch / "source-export"
        self.recovery_store = scratch / "target-store"
        self.source_escrow = scratch / "source-escrow"
        self.config_file = scratch / "config.json"
        self.identity, self.recipient = age_keys(age, scratch / "independent-vault")
        self.point: Completion | None = None
        self.escrow: Escrow | None = None
        self.target_image_ids: dict[str, str] | None = None

    @contextmanager
    def bridge(self, volume: str, image: str, *, readonly: bool):
        name = "prl-offhost-bridge-" + uuid4().hex
        deadline = Deadline(150)
        capabilities = [] if readonly else ["--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE"]
        command(
            [
                "docker",
                "run",
                "--pull",
                "never",
                "-d",
                "--name",
                name,
                "--label",
                "prl.offhost.owner=" + self.owner,
                "--network",
                "none",
                "--user",
                "0",
                "--read-only",
                "--cap-drop",
                "ALL",
                *capabilities,
                "--mount",
                f"type=volume,src={volume},dst=/backups" + (",readonly" if readonly else ""),
                image,
                "python",
                "-c",
                "import time;time.sleep(600)",
            ],
            deadline,
        )
        try:
            yield name
        finally:
            item = json.loads(capture(["docker", "inspect", name], Deadline(20)))[0]
            if item["Config"]["Labels"].get("prl.offhost.owner") != self.owner:
                raise RemoteError(Code.IDENTITY)
            command(["docker", "rm", "--force", item["Id"]], Deadline(20))

    def cli(
        self,
        operation: str,
        store: Path,
        *,
        backup_id: str | None = None,
        release: Path | None = None,
        output_overlay: Path | None = None,
        output_configuration: Path | None = None,
    ) -> Report:
        args = [
            sys.executable,
            "-m",
            "tools.backup.offhost",
            operation,
            "--config",
            str(self.config_file),
            "--credentials",
            str(self.credentials),
            "--age",
            str(self.age),
            "--identity",
            str(self.identity),
            "--local-store",
            str(store),
        ]
        if backup_id is not None:
            args += ["--backup-id", backup_id]
        if operation in {"prepare", "upload"}:
            args += ["--escrow", str(self.source_escrow)]
        if release is not None:
            args += ["--release", str(release)]
        if output_overlay is not None:
            args += ["--output-overlay", str(output_overlay)]
        if output_configuration is not None:
            args += ["--output-configuration", str(output_configuration)]
        return cli_report(args, operation, Deadline(960))

    def publish(self, source: RecoveryStack, backup: dict, source_images: dict) -> None:
        directory(self.local_store)
        backup_id = backup["backup_id"]
        if not BACKUP_ID.fullmatch(backup_id):
            raise RemoteError(Code.IDENTITY)
        self.source_volume = source.env["PRL_BACKUP_VOLUME"]
        with self.bridge(self.source_volume, source_images["api"], readonly=True) as bridge:
            command(
                [
                    "docker",
                    "cp",
                    f"{bridge}:/backups/{backup_id}",
                    str(self.local_store / backup_id),
                ],
                Deadline(120),
            )
        local = BackupConfig(
            "mysql+asyncmy://offline@source-unavailable/prl",
            self.local_store,
            max_bytes=4 * 1024**3,
        )
        manifest = load_artifact(local, backup_id)
        self.config, self.credentials = self.lab.configure(
            source_identity(manifest), self.recipient, self.scratch / "credentials.json"
        )
        write_private(self.config_file, canonical(self.config))
        selected = self.scratch / "release.json"
        write(selected, self.manifest)
        self.cli("prepare", self.local_store, release=selected)
        self.cli("upload", self.local_store, backup_id=backup_id)
        remote = S3(self.config, Deadline(30), credentials=self.credentials)
        point = read_point(remote, backup_id)
        if point is None:
            raise RemoteError(Code.INTEGRITY)
        self.point = point[0]

    @contextmanager
    def recovery_engine(self):
        inputs = directory(self.scratch / "target-runtime")
        stage("start_empty_containerd_recovery_engine")
        with empty_recovery_daemon(inputs):
            command(
                [
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    "prl.backup.storage=1",
                    "--label",
                    "prl.offhost.owner=" + self.owner,
                    self.recovery_volume,
                ],
                Deadline(20),
            )
            yield inputs / "application"

    def freeze_source(self, source: RecoveryStack) -> None:
        from tests.containers.test_compose_acceptance import require_success

        require_success(
            source.compose("stop", "api", "worker", "frontend", "gateway", timeout=90),
            "Source quiescence failed",
        )

    def isolate(self, source: RecoveryStack) -> None:
        from tests.containers.test_compose_acceptance import require_success

        require_success(
            source.compose("stop", "mysql", timeout=90), "Source database isolation failed"
        )
        command(["docker", "stop", self.artifacts.name], Deadline(30))
        for service in ("api", "worker", "frontend", "gateway", "mysql"):
            identifier = require_success(
                source.compose("ps", "--all", "-q", service), "Source isolation inspection failed"
            )
            value = capture(
                ["docker", "inspect", "--format", "{{json .State.Running}}", identifier],
                Deadline(10),
            )
            if value.strip() != b"false":
                raise RemoteError(Code.IDENTITY)
        # Neither path is passed to retrieval; quarantine the only local export as well.
        for path in (self.local_store, self.source_escrow):
            if path.resolve().parent != self.scratch.resolve():
                raise RemoteError(Code.IDENTITY)
            path.rename(path.with_name(path.name + "-unavailable"))
        self.source_isolated = True

    def overrides(self, stack: RecoveryStack) -> None:
        if self.escrow is None:
            raise RemoteError(Code.IMAGE)
        path = self.scratch / "verification-images.yaml"
        path.write_bytes(image_overlay(self.escrow, self.manifest))
        stack.base += ["-f", str(path)]

    def retrieve(self, target: RecoveryStack, backup_id: str) -> None:
        if not self.source_isolated or target.env["PRL_BACKUP_VOLUME"] == self.source_volume:
            raise RemoteError(Code.IDENTITY)
        self.cli("retrieve", self.recovery_store, backup_id=backup_id)
        overlay = target.scratch / "verified-images.yaml"
        public_configuration = target.scratch / "retrieved-configuration"
        report = self.cli(
            "import-images",
            self.recovery_store,
            backup_id=backup_id,
            output_overlay=overlay,
            output_configuration=public_configuration,
        )
        if (
            not report.recovery_overlay_written
            or not report.recovery_configuration_written
            or report.imported_image_ids is None
        ):
            raise RemoteError(Code.IMAGE)
        self.target_image_ids = report.imported_image_ids
        escrow_path = self.recovery_store / ".offhost-escrow" / backup_id
        self.escrow = contract((escrow_path / "escrow.json").read_bytes(), Escrow)
        # Keep the nested engine's administration socket on its owned MySQL
        # volume. The server and recovery SQL client use the same explicit path.
        daemon_overlay = target.scratch / "recovery-daemon.yaml"
        write_private(
            daemon_overlay,
            b"services:\n"
            b"  mysql:\n"
            b"    command: [mysqld, '--socket=/var/lib/mysql/prl-recovery.sock']\n"
            b"    environment:\n"
            b"      PRL_MYSQL_SOCKET: /var/lib/mysql/prl-recovery.sock\n",
        )
        target.base += ["-f", str(daemon_overlay), "-f", str(overlay)]
        downloaded = public_configuration / "tools/deploy"
        for index, value in enumerate(target.base):
            selected = Path(value)
            if selected.is_absolute() and selected.is_relative_to(DEPLOY):
                path = downloaded / selected.relative_to(DEPLOY)
                if not path.is_file():
                    raise RemoteError(Code.IMAGE)
                target.base[index] = str(path)
        target.deployment_root = downloaded
        verify_mysql_bootstrap_mount(target)
        with self.bridge(
            self.recovery_volume, self.target_image_ids["backend"], readonly=False
        ) as bridge:
            command(
                [
                    "docker",
                    "cp",
                    str(self.recovery_store / backup_id),
                    f"{bridge}:/backups/{backup_id}",
                ],
                Deadline(120),
            )
            command(
                ["docker", "exec", bridge, "chown", "-R", "10001:10001", "/backups/" + backup_id],
                Deadline(30),
            )
        self.target_isolated = True

    def validate_images(self, source: dict[str, str], target: dict[str, str]) -> dict:
        if self.escrow is None or self.target_image_ids is None:
            raise RemoteError(Code.IMAGE)
        names = {service: self.manifest.services.get(service) or service for service in source}
        configurations = {
            service: self.escrow.images[name].config_id for service, name in names.items()
        }
        runtime = {service: self.target_image_ids[name] for service, name in names.items()}
        if source != configurations or target != runtime or source["api"] == target["api"]:
            raise RemoteError(Code.IMAGE)
        return dict(
            source_store="classic",
            target_store="containerd",
            target_initially_empty=True,
            archive_configuration_ids=configurations,
            imported_target_ids=runtime,
        )

    def resume_source_for_verification(self, source: RecoveryStack) -> None:
        from tests.containers.test_compose_acceptance import require_success

        self.overrides(source)
        require_success(
            source.compose(
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "180",
                "mysql",
                timeout=240,
            ),
            "Read-only source verification startup failed",
        )


def rehearse(scratch: Path, lab: StoreLab, age: Path) -> tuple[dict, Completion, dict, str]:
    directory(scratch)
    if containerd_store(Deadline(10)):
        raise RemoteError(Code.CONFIGURATION)
    command(["docker", "pull", "--platform", "linux/amd64", ENGINE], Deadline(180))
    source_sha = capture(["git", "rev-parse", "HEAD"], Deadline(10)).decode().strip()
    artifacts = Artifacts(scratch / "artifacts", source_sha)
    try:
        artifacts.start()
        _, manifest, _, _ = artifacts.releases(ROOT)
        artifacts.registry.verify_images(manifest, pull=True)
        for reference in manifest.vendor_images.values():
            command(["docker", "pull", "--platform", "linux/amd64", reference], Deadline(180))
        transport = RemoteRecovery(scratch / "transport", lab, age, artifacts, manifest)

        def factory(path, *, recovered=False):
            return ImmutableRecoveryStack(path, manifest, recovered=recovered)

        report = run(scratch / "service", stack_factory=factory, transport=transport)
        if (
            transport.point is None
            or not transport.source_isolated
            or not transport.target_isolated
        ):
            raise RemoteError(Code.INTEGRITY)
        return (
            report,
            transport.point,
            dict(
                source_services_stopped=True,
                source_backup_unmounted=True,
                source_export_quarantined=True,
                registry_unavailable=True,
                images_imported_from_offhost=True,
                configuration_retrieved_from_offhost=True,
                source_unchanged=report["source_unchanged"],
            ),
            manifest.configuration.sha256,
        )
    finally:
        artifacts.close()
