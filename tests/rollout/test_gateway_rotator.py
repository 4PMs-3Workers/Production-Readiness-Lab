"""Real pinned-Nginx component proof for the production gateway/rotator lifecycle."""

import json
import os
import time
from uuid import uuid4

import yaml
from tests.backend.unit.rollout_fixtures import release
from tests.containers.test_compose_acceptance import AcceptanceFailure, invoke, require_success
from tools.deploy.release_manifest import ROOT
from tools.deploy.rollout_runtime import Deadline, ReleaseRuntime


def test_gateway_replacement_rebinds_rotator_and_rotation_continues(tmp_path):
    project = "prl45-rotator-" + uuid4().hex
    manifest = release(observability=True)
    image = manifest.vendor_images["gateway"]
    nginx = tmp_path / "nginx.conf"
    rotate = tmp_path / "rotate.sh"
    rotate.write_text(
        (ROOT / "tools/infra/gateway-log-rotate.sh").read_text(encoding="utf-8-sig"),
        encoding="utf-8",
        newline="\n",
    )
    nginx.write_text(
        "worker_processes 1;\npid /tmp/nginx.pid;\nerror_log /dev/stderr;\n"
        "events { worker_connections 64; }\n"
        'http { log_format tiny \'{"route":"$uri"}\';\n'
        "client_body_temp_path /tmp/client; proxy_temp_path /tmp/proxy;\n"
        "fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi;\n"
        "scgi_temp_path /tmp/scgi;\n"
        "access_log /var/log/prl/access.jsonl tiny;\n"
        "server { listen 8080; location / { return 200 'ok'; } } }\n",
        encoding="utf-8",
        newline="\n",
    )
    config = dict(
        services=dict(
            gateway=dict(
                image=image,
                user="101:101",
                init=True,
                entrypoint=["nginx", "-g", "daemon off;"],
                volumes=[
                    f"{nginx.resolve()}:/etc/nginx/nginx.conf:ro",
                    "gateway_logs:/var/log/prl",
                ],
            ),
            gateway_log_rotate=dict(
                image=image,
                user="101:101",
                init=True,
                entrypoint=["/bin/sh", "/rotate.sh"],
                pid="service:gateway",
                network_mode="service:gateway",
                depends_on=["gateway"],
                environment=dict(
                    PRL_GATEWAY_ROTATE_BYTES="256",
                    PRL_GATEWAY_ROTATE_FILES="2",
                    PRL_GATEWAY_ROTATE_INTERVAL="0.05",
                ),
                volumes=[
                    "gateway_logs:/var/log/prl",
                    f"{rotate.resolve()}:/rotate.sh:ro",
                ],
            ),
        ),
        volumes=dict(gateway_logs={}),
    )
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    host = ReleaseRuntime(
        ["docker", "compose", "--project-name", project],
        dict(os.environ),
        "http://localhost",
        project,
        tmp_path,
        observability=True,
    )
    # Component-only fixture: no application images are built, pulled or claimed as proof.
    host.manifest, host.overlay_path = manifest, compose_file
    try:
        host.command(["docker", "pull", image])
        host.compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--user",
            "0:0",
            "--entrypoint",
            "/bin/sh",
            "gateway",
            "-c",
            "chown 101:101 /var/log/prl",
        )
        previous = None
        for _ in range(2):
            host.recreate_gateway()
            host.verify_gateway_helpers()
            gateway, helper = host.inspect("gateway"), host.inspect("gateway_log_rotate")
            assert gateway is not None and helper is not None
            current = (gateway["id"], helper["id"])
            if previous is not None:
                assert all(before != after for before, after in zip(previous, current, strict=True))
            previous = current
            marker = uuid4().hex
            host.command(
                [
                    "docker",
                    "exec",
                    gateway["id"],
                    "/bin/sh",
                    "-ec",
                    "i=0; while [ $i -lt 40 ]; do "
                    f"wget -q -O /dev/null http://127.0.0.1:8080/{marker}; "
                    "i=$((i+1)); done",
                ]
            )
            expires = time.monotonic() + 30
            while True:
                rotated = invoke(
                    [
                        "docker",
                        "exec",
                        gateway["id"],
                        "/bin/sh",
                        "-ec",
                        "test -f /var/log/prl/access.jsonl && "
                        f"grep -q {marker} /var/log/prl/access.jsonl.1",
                    ]
                )
                if rotated.returncode == 0:
                    break
                if time.monotonic() >= expires:
                    raise AcceptanceFailure(
                        "Owned rotator did not rotate/reopen after gateway replacement"
                    )
                time.sleep(0.1)
            host.verify_gateway_helpers()
    except Exception:
        diagnostic = invoke(host.base + ["-f", str(compose_file), "logs", "--no-color"])
        (tmp_path / "private-runtime.log").write_text(
            diagnostic.stdout + diagnostic.stderr, encoding="utf-8"
        )
        raise
    finally:
        host.deadline = Deadline(120)
        ids = require_success(
            invoke(
                ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
            ),
            "Owned rotator resource inventory failed",
        ).splitlines()
        for identifier in ids:
            record = json.loads(
                require_success(
                    invoke(["docker", "inspect", identifier]), "Owned container inspect failed"
                )
            )[0]
            labels = record["Config"]["Labels"]
            if labels.get("com.docker.compose.project") != project or labels.get(
                "com.docker.compose.service"
            ) not in {"gateway", "gateway_log_rotate"}:
                raise AcceptanceFailure("Rotator cleanup container ownership mismatch")
        for kind, name in (
            ("volume", project + "_gateway_logs"),
            ("network", project + "_default"),
        ):
            inspected = invoke(["docker", kind, "inspect", name])
            if inspected.returncode == 0:
                labels = json.loads(inspected.stdout)[0]["Labels"]
                if labels.get("com.docker.compose.project") != project:
                    raise AcceptanceFailure("Rotator cleanup storage/network ownership mismatch")
        host.compose("down", "--volumes", "--remove-orphans")
