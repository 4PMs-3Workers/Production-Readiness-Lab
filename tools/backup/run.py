"""Bounded Linux/demo invoker; only an explicitly owned one-off job is removed."""

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SAFE_REPORT_KEYS = {
    "operation",
    "success",
    "reason",
    "duration_seconds",
    "backup_id",
    "completed_at",
    "schema_revision",
    "migration_sha256",
    "source_sha",
    "image_id",
    "snapshot",
    "table_count",
    "total_rows",
    "total_bytes",
    "manifest_sha256",
    "artifact_integrity_verified",
    "application_rehearsal_verified",
    "removed_count",
    "preserved_count",
    "ignored_entries",
}


class JobFailure(Exception):
    pass


def terminate(signum, frame) -> None:
    raise JobFailure()


def invoke(args: list[str], env: dict[str, str], timeout: int = 10):
    return subprocess.run(
        args, env=env, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )


def inspected(args: list[str], env: dict[str, str]) -> dict:
    result = invoke(["docker", *args], env)
    if result.returncode:
        raise JobFailure()
    value = json.loads(result.stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise JobFailure()
    return value[0]


def configuration(env: dict[str, str]) -> tuple[list[str], int]:
    project, tag = env.get("PRL_BACKUP_PROJECT", ""), env.get("PRL_IMAGE_TAG", "")
    sha = env.get("PRL_SOURCE_SHA", "")
    if (
        not re.fullmatch(r"[a-z][a-z0-9-]{0,49}", project)
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag)
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
    ):
        raise JobFailure()
    for role in ("DB", "RUNTIME", "MIGRATION", "SEED", "BACKUP"):
        path = Path(env.get(f"PRL_{role}_ENV_FILE", ""))
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise JobFailure()
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise JobFailure()
    volume_name = env.get("PRL_BACKUP_VOLUME", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", volume_name):
        raise JobFailure()
    if volume_name == f"{project}_mysql_data":
        raise JobFailure()
    volume = inspected(["volume", "inspect", volume_name], env)
    if (volume.get("Labels") or {}).get("prl.backup.storage") != "1":
        raise JobFailure()
    image = inspected(["image", "inspect", f"prl-backend:{tag}"], env)
    if (image.get("Config", {}).get("Labels") or {}).get(
        "org.opencontainers.image.revision"
    ) != sha or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", "")):
        raise JobFailure()
    env["PRL_BACKEND_IMAGE_ID"] = image["Id"]
    timeout = int(env.get("PRL_BACKUP_JOB_SECONDS", "1300"))
    if not 30 <= timeout <= 7500:
        raise JobFailure()
    command = ["docker", "compose", "--project-name", project]
    for name in ("compose.yaml", "compose.local.yaml", "compose.backup.yaml"):
        command.extend(["-f", str(ROOT / "tools/deploy" / name)])
    return command, timeout


def remove_owned(name: str, token: str, env: dict[str, str]) -> None:
    try:
        container = inspected(["container", "inspect", name], env)
        if (
            container.get("Name") == f"/{name}"
            and (container.get("Config", {}).get("Labels") or {}).get("prl.backup.job") == token
        ):
            invoke(["docker", "container", "rm", "--force", name], env, 20)
    except (JobFailure, OSError, ValueError, subprocess.TimeoutExpired):
        pass


def main() -> int:
    env = dict(os.environ)
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        env.pop(key, None)
    token = uuid4().hex
    name = "prl-backup-job-" + token
    try:
        command, timeout = configuration(env)
        result = invoke(
            [
                *command,
                "--profile",
                "backup",
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                name,
                "--label",
                f"prl.backup.job={token}",
                "backup",
                "scheduled",
            ],
            env,
            timeout,
        )
        if len(result.stdout) > 65536:
            raise JobFailure()
        reports = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if not reports or any(
            not isinstance(report, dict) or not set(report) <= SAFE_REPORT_KEYS
            for report in reports
        ):
            raise JobFailure()
        for report in reports:
            print(json.dumps(report, sort_keys=True, allow_nan=False), flush=True)
        return 0 if result.returncode == 0 else 1
    except (JobFailure, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print(json.dumps(dict(operation="scheduled", success=False, reason="HOST_JOB_FAILED")))
        return 1
    finally:
        remove_owned(name, token, env)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
