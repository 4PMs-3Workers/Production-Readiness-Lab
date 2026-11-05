"""UUID-owned Linux OCI protocol fixtures, never production publication/review proof."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

from tests.containers.test_compose_acceptance import ComposeAcceptance, invoke, require_success
from tools.backup.recovery_config import render as gateway_config
from tools.deploy.release import render
from tools.deploy.release_images import Registry, archive_image, command
from tools.deploy.release_manifest import (
    CHECKS,
    SCHEMA,
    Image,
    Manifest,
    Spec,
    canonical,
    configuration,
    parse,
    proof,
    vendor_inventory,
)

REGISTRY_IMAGE = "registry:2.8.3"
OWNER_LABEL = "org.prl.issue45.owner"
BAD_API = """
import re
import uvicorn
from starlette.responses import JSONResponse
from app.main import app
@app.middleware('http')
async def fail_after_booking(request,call_next):
    path=request.url.path
    detail=request.method=='GET' and re.fullmatch(r'/api/v1/appointments/[0-9a-f-]{36}',path)
    cancel=request.method=='POST' and path.endswith('/cancel')
    if detail or cancel:
        return JSONResponse({'code':'fixture-failed'},status_code=503)
    return await call_next(request)
# Real /health/ready, login, booking and replay; no raw access/body logging.
uvicorn.run(app,host='0.0.0.0',port=8000,proxy_headers=False,access_log=False,log_level='warning')
"""


class Artifacts:
    def __init__(self, directory: Path, source_sha: str):
        self.directory, self.source_sha = directory, source_sha
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner = uuid4().hex
        self.name = "prl-rollback-registry-" + self.owner
        self.builder = "prl-rollback-builder-" + self.owner
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.repository = f"127.0.0.1:{self.port}/prl/{self.owner}"
        self.registry = Registry(plain_http=True)
        self.container: str | None = None
        self.builder_created = False
        self.references: list[str] = []

    def start(self) -> None:
        self.container = (
            command(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    self.name,
                    "--label",
                    f"{OWNER_LABEL}={self.owner}",
                    "--publish",
                    f"127.0.0.1:{self.port}:5000",
                    REGISTRY_IMAGE,
                ]
            )
            .decode()
            .strip()
        )
        end = time.monotonic() + 45
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{self.port}/v2/", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= end:
                    raise RuntimeError("Loopback release registry failed readiness") from None
                time.sleep(0.5)
        config = self.directory / "buildkit.toml"
        config.write_text(f'[registry."127.0.0.1:{self.port}"]\n  http = true\n', encoding="utf-8")
        command(
            [
                "docker",
                "buildx",
                "create",
                "--name",
                self.builder,
                "--driver",
                "docker-container",
                "--driver-opt",
                "network=host",
                "--buildkitd-config",
                str(config),
            ]
        )
        self.builder_created = True

    def build(self, name: str, dockerfile: Path, context: Path) -> Image:
        archive = self.directory / f"{name}.tar"
        command(
            [
                "docker",
                "buildx",
                "build",
                "--builder",
                self.builder,
                "--platform",
                "linux/amd64",
                "--provenance=false",
                "--sbom=false",
                "--build-arg",
                f"PRL_SOURCE_SHA={self.source_sha}",
                "-f",
                str(dockerfile),
                "--output",
                "type=oci,dest=" + str(archive),
                str(context),
            ],
            timeout=900,
        )
        image = archive_image(archive, self.repository + "/" + name, self.source_sha)
        self.registry.copy_archive(archive, image, self.source_sha, name)
        self.references.append(image.reference)
        return image

    def releases(self, root: Path) -> tuple[str, Manifest, str, Manifest]:
        backend = self.build("backend", root / "Dockerfile.backend", root)
        frontend = self.build("frontend", root / "Dockerfile.frontend", root)
        bad = self.directory / "bad"
        bad.mkdir()
        (bad / "Dockerfile").write_text(
            "FROM "
            + backend.reference
            + "\nLABEL org.prl.fixture=known-bad-business-api\nCMD "
            + json.dumps(["python", "-c", BAD_API])
            + "\n",
            encoding="utf-8",
        )
        broken = self.build("bad-backend", bad / "Dockerfile", bad)
        stable = self.manifest(1, backend, frontend, None)
        stable_ref = self.registry.publish(self.repository + "/releases", stable)
        failed = self.manifest(2, broken, frontend, stable_ref)
        failed_ref = self.registry.publish(self.repository + "/releases", failed)
        return stable_ref, stable, failed_ref, failed

    def manifest(
        self, number: int, backend: Image, frontend: Image, previous: str | None
    ) -> Manifest:
        # Strict fixture metadata exercises #44's delivered reader and OCI clients.
        # These simulated checks/review fields must never be called real release proof.
        data = dict(
            format="prl-candidate",
            format_version=1,
            release_id=f"release-{number}-1",
            created_at="2026-11-05T00:00:00Z",
            provenance=dict(
                repository="4PMs-3Workers/Production-Readiness-Lab",
                source_sha=self.source_sha,
                pr_number=1,
                pr_head_sha=self.source_sha,
                pr_merge_sha=self.source_sha,
                run_id=number,
                run_attempt=1,
                workflow_ref="4PMs-3Workers/Production-Readiness-Lab/.github/workflows/release.yml@refs/heads/develop",
            ),
            images=dict(backend=backend.model_dump(), frontend=frontend.model_dump()),
            services=dict(
                dict.fromkeys(["api", "worker", "schema", "migrate", "seed", "backup"], "backend"),
                frontend="frontend",
            ),
            vendor_images=vendor_inventory(False),
            accepted_schema_revisions=[SCHEMA],
            configuration=configuration("local", False).model_dump(),
            previous_stable=previous,
            bootstrap=previous is None,
        )
        spec = parse(canonical(data), Spec)
        return parse(
            canonical(
                dict(
                    data,
                    format="prl-release",
                    candidate_artifact_id=1,
                    candidate_proof=proof(spec, "candidate").model_dump(),
                    published_proof=proof(spec, "published").model_dump(),
                    source_checks=sorted(CHECKS),
                )
            ),
            Manifest,
        )

    def close(self) -> None:
        if self.builder_created:
            command(["docker", "buildx", "rm", "--force", self.builder])
        inspected = invoke(["docker", "inspect", self.name])
        if inspected.returncode == 0:
            record = json.loads(inspected.stdout)[0]
            identifier = record.get("Id")
            if (
                not isinstance(identifier, str)
                or not re.fullmatch(r"[0-9a-f]{64}", identifier)
                or self.container is not None
                and record.get("Id") != self.container
                or record.get("Name") != "/" + self.name
                or record["Config"]["Labels"].get(OWNER_LABEL) != self.owner
            ):
                raise RuntimeError("Release registry cleanup ownership mismatch")
            command(["docker", "rm", "--force", "--volumes", identifier])
        for reference in self.references:
            # Delete only this UUID repository reference, without force/prune.
            invoke(["docker", "image", "rm", reference])


class RolloutStack(ComposeAcceptance):
    def __init__(self, directory: Path, manifest: Manifest):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        super().__init__(directory)
        self.env.update(gateway_config(directory / "nginx", "172.30.45.0/29"))
        self.base.extend(
            [
                "-f",
                str(Path(__file__).resolve().parents[2] / "tools/deploy/compose.recovery.yaml"),
            ]
        )
        self.manifest = manifest
        self.select_manifest(manifest)

    def _ensure_gateway_subnet_available(self, *, additional_subnets=()) -> None:
        reserved = ipaddress.ip_network("172.30.45.0/29")
        ids = require_success(invoke(["docker", "network", "ls", "-q"]), "Network inventory failed")
        for identifier in ids.splitlines():
            records = json.loads(
                require_success(
                    invoke(["docker", "network", "inspect", identifier]),
                    "Network inspection failed",
                )
            )
            for entry in (records[0].get("IPAM") or {}).get("Config") or []:
                subnet = entry.get("Subnet")
                if subnet and ipaddress.ip_network(subnet).overlaps(reserved):
                    raise RuntimeError("The rollout fixture subnet is already owned")

    def select_manifest(self, manifest: Manifest) -> None:
        path = self.scratch / "initial-release.yaml"
        path.write_bytes(render(manifest))
        self.base.extend(["-f", str(path)])

    def start(self) -> None:
        # No builds here: all application bytes have already passed through the
        # isolated registry and the dependency's exact digest/config-ID verifier.
        require_success(
            self.compose(
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "180",
                "mysql",
                timeout=240,
            ),
            "DB setup failed",
        )
        require_success(
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
                timeout=150,
            ),
            "Fixture migration failed",
        )
        require_success(
            self.compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh"),
            "Fixture grants failed",
        )
        require_success(
            self.compose(
                "--profile",
                "seed",
                "run",
                "--pull",
                "never",
                "--no-deps",
                "--rm",
                "-T",
                "seed",
                timeout=150,
            ),
            "Fictional seed failed",
        )
        require_success(
            self.compose("run", "--pull", "never", "--no-deps", "--rm", "-T", "schema"),
            "Fixture schema failed",
        )
        require_success(
            self.compose(
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--no-deps",
                "api",
                "worker",
                "frontend",
                "gateway",
                timeout=180,
            ),
            "Fixture services failed",
        )
        self.started = True
        self.wait_gateway()
        self.wait_heartbeat()
