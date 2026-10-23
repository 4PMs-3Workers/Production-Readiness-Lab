"""Strict, secret-free deployment provenance with atomic replacement.

Only explicitly validated values can enter the manifest. Callers hold lifecycle_lock
across inspection and replacement so concurrent operations cannot lose updates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_REVISION = "20261019_1200_proposal_replay"
COMMANDS = {"config", "build", "migrate", "seed", "up", "stop", "down", "status", "evidence"}
SERVICES = {"mysql", "api", "worker", "frontend", "gateway"}


def timestamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate(value):
    required = {
        "manifest_version",
        "source_sha",
        "overlay",
        "schema_revisions",
        "images",
        "verification",
        "created_at",
        "updated_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Invalid manifest fields")
    if type(value["manifest_version"]) is not int or value["manifest_version"] != 1:
        raise ValueError("Invalid manifest version")
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
    check = value["verification"]
    if (
        not isinstance(check, dict)
        or set(check) != {"command", "status"}
        or check["command"] not in COMMANDS
        or check["status"] not in {"passed", "failed"}
    ):
        raise ValueError("Invalid verification")
    images = value["images"]
    if not isinstance(images, dict) or not set(images) <= SERVICES:
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
    return value


def create(sha, overlay, command, status, images, previous=None):
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
