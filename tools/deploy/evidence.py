"""Strict, secret-free deployment provenance with atomic replacement.

Version 1 remains readable with its historical exact schema. Version 2 records
explicit observability mode, allowlisted public-configuration identity and the
selected stack's lifecycle state. Callers hold lifecycle_lock across updates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_REVISION = "20261019_1200_proposal_replay"
COMMANDS = {"config", "build", "migrate", "seed", "up", "stop", "down", "status", "evidence"}
SERVICES = {"mysql", "api", "worker", "frontend", "gateway"}
OPERATIONAL_SERVICES = {"migrate", "schema", "seed"}
OBSERVABILITY_SERVICES = {
    "collector",
    "prometheus",
    "loki",
    "tempo",
    "grafana",
    "alertmanager",
    "local_alert_receiver",
    "gateway_log_rotate",
    "backup_metrics_exporter",
}
V2_SERVICES = SERVICES | OPERATIONAL_SERVICES | OBSERVABILITY_SERVICES
V1_FIELDS = {
    "manifest_version",
    "source_sha",
    "overlay",
    "schema_revisions",
    "images",
    "verification",
    "created_at",
    "updated_at",
}
V2_FIELDS = V1_FIELDS | {
    "observability",
    "configuration_sha256",
    "observability_verification",
    "deployment_state",
    "target_origin",
}
V2_LEGACY_FIELDS = V2_FIELDS - {"target_origin"}
TARGET_ATTESTATION_SERVICES = {"api", "frontend", "worker", "gateway"}


def timestamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_image_inventory(images, version):
    allowed = SERVICES if version == 1 else V2_SERVICES
    if not isinstance(images, dict) or not set(images) <= allowed:
        raise ValueError("Invalid images")
    for image in images.values():
        if not isinstance(image, dict) or set(image) != {"id", "digests"}:
            raise ValueError("Invalid image fields")
        if not isinstance(image["id"], str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", image["id"]
        ):
            raise ValueError("Invalid image ID")
        if not isinstance(image["digests"], list) or any(
            not isinstance(digest, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", digest)
            for digest in image["digests"]
        ):
            raise ValueError("Invalid image digests")


def _validate_check(check, statuses):
    if (
        not isinstance(check, dict)
        or set(check) != {"command", "status"}
        or check["command"] not in COMMANDS
        or check["status"] not in statuses
    ):
        raise ValueError("Invalid verification")


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid manifest fields")
    version = value.get("manifest_version")
    required = (
        V1_FIELDS
        if type(version) is int and version == 1
        else (V2_FIELDS if type(version) is int and version == 2 else None)
    )
    if required is None or (
        set(value) != required and not (version == 2 and set(value) == V2_LEGACY_FIELDS)
    ):
        raise ValueError("Invalid manifest fields")
    if not isinstance(value["source_sha"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", value["source_sha"]
    ):
        raise ValueError("Invalid source revision")
    if value["overlay"] not in ("local", "https"):
        raise ValueError("Invalid overlay")
    if value["schema_revisions"] != [SCHEMA_REVISION]:
        raise ValueError("Invalid schema revisions")
    for key in ("created_at", "updated_at"):
        if not isinstance(value[key], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value[key]
        ):
            raise ValueError("Invalid timestamp")
        datetime.fromisoformat(value[key].replace("Z", "+00:00"))
    _validate_check(value["verification"], {"passed", "failed"})
    _validate_image_inventory(value["images"], version)
    if version == 2:
        if type(value["observability"]) is not bool:
            raise ValueError("Invalid observability mode")
        if not isinstance(value["configuration_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", value["configuration_sha256"]
        ):
            raise ValueError("Invalid configuration identity")
        if value["deployment_state"] not in {"active", "stopped", "down", "unknown"}:
            raise ValueError("Invalid deployment state")
        target_origin = value.get("target_origin")
        if target_origin is not None:
            try:
                parsed_origin = urlsplit(target_origin)
                _ = parsed_origin.port
            except (TypeError, ValueError):
                raise ValueError("Invalid target origin") from None
            if (
                not isinstance(target_origin, str)
                or parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("Invalid target origin")
        telemetry = value["observability_verification"]
        if not value["observability"] and telemetry is not None:
            raise ValueError("Disabled observability has telemetry evidence")
        if telemetry is not None:
            if telemetry.get("command") != "evidence":
                raise ValueError("Invalid telemetry verification command")
            _validate_check(telemetry, {"passed", "degraded", "failed"})
    return value


def create(sha, overlay, command, status, images, previous=None):
    """Create a historical version-1 record (used only by compatibility callers)."""
    now = timestamp()
    if previous is not None:
        validate(previous)
        if previous["source_sha"] != sha:
            raise ValueError("Manifest revision mismatch")
    return validate(
        {
            "manifest_version": 1,
            "source_sha": sha,
            "overlay": overlay,
            "schema_revisions": [SCHEMA_REVISION],
            "images": images,
            "verification": {"command": command, "status": status},
            "created_at": previous["created_at"] if previous else now,
            "updated_at": now,
        }
    )


def create_v2(
    sha,
    overlay,
    observability,
    configuration_sha256,
    command,
    status,
    images,
    *,
    observability_verification=None,
    deployment_state="unknown",
    target_origin=None,
    previous=None,
):
    now = timestamp()
    if previous is not None:
        validate(previous)
        if previous["source_sha"] != sha:
            raise ValueError("Manifest revision mismatch")
    return validate(
        {
            "manifest_version": 2,
            "source_sha": sha,
            "overlay": overlay,
            "schema_revisions": [SCHEMA_REVISION],
            "images": images,
            "verification": {"command": command, "status": status},
            "created_at": previous["created_at"] if previous else now,
            "updated_at": now,
            "observability": observability,
            "configuration_sha256": configuration_sha256,
            "observability_verification": observability_verification,
            "deployment_state": deployment_state,
            "target_origin": target_origin,
        }
    )


def create_target_attestation(manifest):
    """Create the public runtime identity served by the candidate gateway."""
    value = validate(manifest)
    images = value["images"]
    if (
        value["manifest_version"] != 2
        or value["verification"] != {"command": "evidence", "status": "passed"}
        or value["deployment_state"] != "active"
        or not isinstance(value.get("target_origin"), str)
        or not TARGET_ATTESTATION_SERVICES <= images.keys()
    ):
        return {"schema_version": 1, "status": "unavailable"}
    payload = {
        "schema_version": 1,
        "status": "active",
        "source_sha": value["source_sha"],
        "target_origin": value["target_origin"],
        "updated_at": value["updated_at"],
        "configuration_sha256": value["configuration_sha256"],
        "images": {
            name: {"id": images[name]["id"], "digests": sorted(images[name]["digests"])}
            for name in sorted(TARGET_ATTESTATION_SERVICES)
        },
    }
    fingerprint = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return validate_target_attestation({**payload, "attestation_sha256": fingerprint})


def validate_target_attestation(value):
    fields = {
        "schema_version",
        "status",
        "source_sha",
        "target_origin",
        "updated_at",
        "configuration_sha256",
        "images",
        "attestation_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid target attestation fields")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["status"] != "active"
        or not isinstance(value["source_sha"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", value["source_sha"])
        or not isinstance(value["configuration_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["configuration_sha256"])
        or not isinstance(value["updated_at"], str)
        or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value["updated_at"]
        )
    ):
        raise ValueError("Invalid target attestation identity")
    origin = value["target_origin"]
    parsed_origin = urlsplit(origin) if isinstance(origin, str) else None
    if (
        parsed_origin is None
        or parsed_origin.scheme not in {"http", "https"}
        or not parsed_origin.hostname
        or parsed_origin.username is not None
        or parsed_origin.password is not None
        or parsed_origin.path not in {"", "/"}
        or parsed_origin.query
        or parsed_origin.fragment
    ):
        raise ValueError("Invalid target attestation origin")
    images = value["images"]
    _validate_image_inventory(images, 1)
    if set(images) != TARGET_ATTESTATION_SERVICES:
        raise ValueError("Invalid target attestation services")
    fingerprint = value["attestation_sha256"]
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid target attestation fingerprint")
    payload = {key: item for key, item in value.items() if key != "attestation_sha256"}
    if hashlib.sha256(_canonical_json(payload)).hexdigest() != fingerprint:
        raise ValueError("Target attestation fingerprint mismatch")
    return value


def write_target_attestation(path, manifest):
    """Atomically publish non-secret runtime evidence for the read-only gateway mount."""
    _write_public_attestation(path, create_target_attestation(manifest))


def write_unavailable_target_attestation(path):
    """Invalidate a previously published identity before a deployment transition."""
    _write_public_attestation(path, {"schema_version": 1, "status": "unavailable"})


def _write_public_attestation(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".attestation-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def write(path, value):
    validate(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".manifest-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lifecycle_lock(path):
    """Kernel lock released automatically on exit/crash; lock files are never unlinked."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lifecycle_directory(root):
    """One host selection for legacy lifecycle commands and manifest operations."""
    return Path(root) / "build" / "deploy"


def main():
    parser = argparse.ArgumentParser(description="Validate a deployment manifest")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        validate(json.loads(args.manifest.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError):
        print("Invalid deployment manifest")
        return 1
    print("Deployment manifest valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
