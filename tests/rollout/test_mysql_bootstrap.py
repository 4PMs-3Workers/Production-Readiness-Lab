"""Prove the deployed health probe rejects MySQL's temporary init server."""

import json
import os
import secrets
import time
from collections.abc import Callable
from uuid import uuid4

import yaml
from tests.containers.test_compose_acceptance import AcceptanceFailure, invoke, require_success
from tools.deploy.release_manifest import ROOT


def wait_for(check: Callable[[], bool], message: str) -> None:
    expires = time.monotonic() + 90
    while time.monotonic() < expires:
        if check():
            return
        time.sleep(0.25)
    raise AcceptanceFailure(message)


def test_mysql_health_rejects_temporary_server_before_init_scripts_complete(tmp_path):
    config = yaml.safe_load((ROOT / "tools/deploy/compose.yaml").read_text(encoding="utf-8"))
    probe = config["services"]["mysql"]["healthcheck"]["test"][1].replace("$$", "$")
    image = json.loads((ROOT / "tools/infra/images.lock.json").read_text())["images"]["mysql"]
    owner = uuid4().hex
    name = "prl45-bootstrap-" + owner
    script = tmp_path / "00-hold-bootstrap.sh"
    script.write_text(
        "#!/bin/bash\n(\nset -eu\ntouch /tmp/prl-bootstrap-waiting\n"
        "for attempt in {1..600}; do\n"
        "  [ ! -f /tmp/prl-bootstrap-continue ] || break\n"
        "  sleep 0.25\ndone\ntest -f /tmp/prl-bootstrap-continue\n)\n",
        encoding="utf-8",
        newline="\n",
    )
    environment = dict(os.environ, MYSQL_ROOT_PASSWORD=secrets.token_hex(24))
    identifier = None
    try:
        identifier = require_success(
            invoke(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--label",
                    "org.prl.issue45.owner=" + owner,
                    "--network",
                    "none",
                    "--tmpfs",
                    "/var/lib/mysql",
                    "--env",
                    "MYSQL_ROOT_PASSWORD",
                    "--mount",
                    f"type=bind,source={script.resolve()},target=/tmp/prl-hold-bootstrap.sh,readonly",
                    "--entrypoint",
                    "bash",
                    image,
                    "-c",
                    # Force the official entrypoint's sourced-script path on
                    # every host, including Windows bind mounts with +x bits.
                    "cp /tmp/prl-hold-bootstrap.sh /docker-entrypoint-initdb.d/00-hold-bootstrap.sh"
                    " && chmod 0644 /docker-entrypoint-initdb.d/00-hold-bootstrap.sh"
                    " && exec /usr/local/bin/docker-entrypoint.sh mysqld",
                ],
                env=environment,
            ),
            "Bootstrap probe fixture startup failed",
        )
        wait_for(
            lambda: invoke(
                ["docker", "exec", name, "test", "-f", "/tmp/prl-bootstrap-waiting"]
            ).returncode
            == 0,
            "MySQL temporary server never reached the init barrier",
        )
        legacy = (
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot '
            '--connect-timeout=3 --batch --skip-column-names -e "SELECT 1" >/dev/null 2>&1'
        )
        assert invoke(["docker", "exec", name, "bash", "-c", legacy]).returncode == 0
        assert invoke(["docker", "exec", name, "bash", "-c", probe]).returncode != 0
        require_success(
            invoke(["docker", "exec", name, "touch", "/tmp/prl-bootstrap-continue"]),
            "Could not release the owned MySQL init barrier",
        )
        wait_for(
            lambda: invoke(["docker", "exec", name, "bash", "-c", probe]).returncode == 0,
            "MySQL final server did not pass the deployed health probe",
        )
    finally:
        inspected = invoke(
            [
                "docker",
                "inspect",
                "--format",
                '{{.Id}} {{index .Config.Labels "org.prl.issue45.owner"}}',
                name,
            ]
        )
        if inspected.returncode == 0:
            actual, actual_owner = inspected.stdout.strip().split()
            if actual_owner != owner or identifier is not None and actual != identifier:
                raise AcceptanceFailure("Bootstrap probe cleanup ownership mismatch")
            require_success(
                invoke(["docker", "rm", "--force", "--volumes", actual]),
                "Owned bootstrap probe cleanup failed",
            )
