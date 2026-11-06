"""MySQL must not advertise readiness from its socket-only bootstrap server."""

import json
import os

import pytest
from tests.containers.test_compose_acceptance import (
    ComposeAcceptance,
    invoke,
    json_output,
    require_success,
)


def test_mysql_healthcheck_waits_for_final_server(tmp_path):
    if os.environ.get("RUN_BACKUP_REHEARSAL") != "1":
        pytest.fail("RUN_BACKUP_REHEARSAL=1 and local Docker/Compose are required")

    scratch = tmp_path / "stack"
    scratch.mkdir(mode=0o700)
    gate = tmp_path / "gate"
    gate.mkdir(mode=0o755)
    release = gate / "release"
    initializer = tmp_path / "99-readiness-gate.sh"
    initializer.write_text(
        "(\nset -eu\ntouch /tmp/prl-readiness-initializing\n"
        "for attempt in {1..180}; do\n"
        "  if [ -f /prl-readiness/release ]; then exit 0; fi\n"
        "  sleep 1\ndone\nexit 1\n)\n",
        encoding="utf-8",
        newline="\n",
    )
    initializer.chmod(0o644)
    overlay = tmp_path / "readiness.json"
    overlay.write_text(
        json.dumps(
            {
                "services": {
                    "mysql": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(initializer),
                                "target": "/docker-entrypoint-initdb.d/99-readiness-gate.sh",
                                "read_only": True,
                            },
                            {
                                "type": "bind",
                                "source": str(gate),
                                "target": "/prl-readiness",
                                "read_only": True,
                            },
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    stack = ComposeAcceptance(scratch)
    stack.base.extend(["-f", str(overlay)])
    try:
        require_success(stack.compose("up", "-d", "mysql"), "MySQL readiness fixture failed")
        stack.wait_for(
            lambda: stack.compose(
                "exec", "-T", "mysql", "test", "-f", "/tmp/prl-readiness-initializing"
            ).returncode
            == 0,
            seconds=120,
            failure="MySQL bootstrap did not reach the controlled initialization gate",
        )
        # The temporary server accepts root SQL over its socket at this point.
        assert stack.sql("SELECT 1;") == "1"
        container = require_success(stack.compose("ps", "-q", "mysql"), "MySQL identity missing")
        healthcheck = json_output(
            invoke(
                ["docker", "inspect", "--format", "{{json .Config.Healthcheck.Test}}", container]
            ),
            "MySQL healthcheck could not be inspected",
        )
        assert len(healthcheck) == 2 and healthcheck[0] == "CMD-SHELL"

        def probe():
            return stack.compose("exec", "-T", "mysql", "sh", "-c", healthcheck[1], timeout=10)

        assert probe().returncode != 0, "MySQL healthcheck accepted the temporary bootstrap server"
        release.touch()
        require_success(
            stack.compose("up", "-d", "--wait", "--wait-timeout", "180", "mysql", timeout=240),
            "MySQL final server did not become healthy",
        )
        require_success(probe(), "MySQL final server did not pass its configured healthcheck")
        assert stack.sql("SELECT @@global.partial_revokes;") in {"0", "1"}
    finally:
        release.touch(exist_ok=True)
        stack.cleanup()
