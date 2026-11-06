"""Exercise the reviewed restore workflow while source services/storage are unavailable."""

from __future__ import annotations

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
from tools.backup.offhost_escrow import (
    Escrow,
    capture,
    image_overlay,
    materialize_configuration,
    release_at,
)
from tools.backup.offhost_io import Deadline, directory
from tools.backup.offhost_io import run as command
from tools.backup.offhost_lab import StoreLab, age_keys
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import read_point
from tools.backup.rehearsal import DEPLOY, ROOT, RecoveryStack, run
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


class RemoteRecovery:
    def __init__(
        self, scratch: Path, lab: StoreLab, age: Path, artifacts: Artifacts, manifest: Manifest
    ):
        self.scratch, self.lab, self.age = directory(scratch), lab, age
        self.artifacts, self.manifest = artifacts, manifest
        self.owner = uuid4().hex
        self.recovery_volume = "prl-offhost-recovery-" + self.owner
        self.created_volume = False
        self.source_isolated = self.target_isolated = False
        self.local_store = scratch / "source-export"
        self.recovery_store = scratch / "target-store"
        self.source_escrow = scratch / "source-escrow"
        self.config_file = scratch / "config.json"
        self.identity, self.recipient = age_keys(age, scratch / "independent-vault")
        self.point: Completion | None = None
        self.escrow: Escrow | None = None

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
        self.created_volume = True

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
        path = self.scratch / (
            "target-images.yaml" if stack.recovered else "verification-images.yaml"
        )
        path.write_bytes(image_overlay(self.escrow, self.manifest))
        stack.base += ["-f", str(path)]

    def retrieve(self, target: RecoveryStack, backup_id: str) -> None:
        if not self.source_isolated or target.env["PRL_BACKUP_VOLUME"] == self.source_volume:
            raise RemoteError(Code.IDENTITY)
        self.cli("retrieve", self.recovery_store, backup_id=backup_id)
        self.cli("import-images", self.recovery_store, backup_id=backup_id)
        escrow_path = self.recovery_store / ".offhost-escrow" / backup_id
        self.escrow = contract((escrow_path / "escrow.json").read_bytes(), Escrow)
        self.overrides(target)
        public_configuration = target.scratch / "retrieved-configuration"
        materialize_configuration(
            escrow_path,
            release_at(escrow_path / "release.json"),
            public_configuration,
            Deadline(30),
        )
        downloaded = public_configuration / "tools/deploy"
        for index, value in enumerate(target.base):
            selected = Path(value)
            if selected.is_absolute() and selected.is_relative_to(DEPLOY):
                path = downloaded / selected.relative_to(DEPLOY)
                if not path.is_file():
                    raise RemoteError(Code.IMAGE)
                target.base[index] = str(path)
        target.deployment_root = downloaded
        with self.bridge(
            self.recovery_volume, self.escrow.images["backend"].local_id, readonly=False
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

    def close(self) -> None:
        if self.created_volume:
            record = json.loads(
                capture(["docker", "volume", "inspect", self.recovery_volume], Deadline(20))
            )[0]
            if record.get("Labels", {}).get("prl.offhost.owner") != self.owner:
                raise RemoteError(Code.IDENTITY)
            command(["docker", "volume", "rm", self.recovery_volume], Deadline(30))


def rehearse(scratch: Path, lab: StoreLab, age: Path) -> tuple[dict, Completion, dict, str]:
    directory(scratch)
    source_sha = capture(["git", "rev-parse", "HEAD"], Deadline(10)).decode().strip()
    artifacts = Artifacts(scratch / "artifacts", source_sha)
    transport = None
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
        try:
            if transport is not None:
                transport.close()
        finally:
            artifacts.close()
