"""Run the existing local job, then independently copy its verified point off-host."""

import contextlib
import io
import json
import os
import signal
from pathlib import Path

from tools.backup import offhost, run
from tools.backup.offhost_contract import Code, RemoteError


def bind_store(env: dict[str, str]) -> Path:
    path = Path(env.get("PRL_OFFHOST_LOCAL_STORE", ""))
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise RemoteError(Code.CONFIGURATION)
    volume = run.inspected(["volume", "inspect", env.get("PRL_BACKUP_VOLUME", "")], env)
    options = volume.get("Options") or {}
    if (
        (volume.get("Labels") or {}).get("prl.backup.storage") != "1"
        or "bind" not in options.get("o", "").split(",")
        or Path(options.get("device", "")).resolve() != path.resolve()
    ):
        raise RemoteError(Code.CONFIGURATION)
    return path


def main() -> int:
    env = dict(os.environ)
    try:
        if env.get("PRL_OFFHOST_CONFIG"):
            bind_store(env)
        return run_scheduled(env)
    except (RemoteError, run.JobFailure, OSError, ValueError, TypeError, AttributeError):
        print(
            json.dumps(
                dict(operation="offhost-scheduled", success=False, reason=Code.CONFIGURATION.value)
            )
        )
        return 1


def run_scheduled(env: dict[str, str]) -> int:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = run.main()
    reports = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
    for report in reports:
        print(json.dumps(report, sort_keys=True), flush=True)
    if not env.get("PRL_OFFHOST_CONFIG"):
        return result
    candidates = [
        value
        for value in reports
        if value.get("operation") == "backup" and value.get("success") is True
    ]
    if not candidates:
        return result or 1
    bind_store(env)
    remote_result = offhost.main(["upload", "--backup-id", candidates[-1]["backup_id"]])
    if remote_result == 0:
        remote_result = offhost.main(["retain"])
    return result or remote_result


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, run.terminate)
    raise SystemExit(main())
