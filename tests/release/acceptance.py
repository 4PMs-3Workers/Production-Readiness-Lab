"""Consume prebuilt application digests in an isolated real Compose stack."""

from pathlib import Path

from tests.containers.test_compose_acceptance import (
    ComposeAcceptance,
    json_output,
    require_success,
)
from tools.deploy.release import render
from tools.deploy.release_images import Registry, command
from tools.deploy.release_manifest import (
    Spec,
    canonical,
    configuration,
    parse,
    require_configuration,
)


class ReleaseStack(ComposeAcceptance):
    def __init__(self, scratch: Path, spec: Spec):
        self.release_spec = spec
        super().__init__(scratch)
        assert self.expected_sha == spec.provenance.source_sha
        # Core application smoke uses the existing local lab gateway, regardless
        # of the intended deployment profile. Full telemetry/HTTPS regressions
        # remain in the existing source checks; no production measurement claimed.
        local = spec.model_dump()
        local["images"].pop("collector", None)
        local["services"].pop("collector", None)
        local["services"].pop("local_alert_receiver", None)
        local["vendor_images"] = {
            key: value for key, value in spec.vendor_images.items() if key in {"mysql", "gateway"}
        }
        local["configuration"] = configuration("local", False).model_dump()
        overlay = scratch / "release.yaml"
        overlay.write_bytes(render(parse(canonical(local), Spec)))
        self.base.extend(["-f", str(overlay)])

    def start(self) -> None:
        config = json_output(
            self.compose(
                "--profile",
                "operations",
                "--profile",
                "seed",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ),
            "Release Compose config failed",
        )
        services = config["services"]
        assert not any(service.get("build") for service in services.values())
        assert services["api"]["image"] == services["worker"]["image"]
        for name in ("api", "worker", "schema", "migrate", "seed", "frontend"):
            image = self.release_spec.images[self.release_spec.services[name]]
            assert services[name]["image"] == image.reference
        require_success(
            self.compose(
                "up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "mysql", timeout=240
            ),
            "Release database failed",
        )
        # Compose run has no --no-build flag. Build keys were removed above;
        # use only verified local images and avoid dependency-driven operations.
        self.expect_compose_failure("run", "--pull", "never", "--no-deps", "--rm", "-T", "schema")
        for name in ("migrate",):
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
                    name,
                    timeout=240,
                ),
                "Release migration failed",
            )
        require_success(
            self.compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh"),
            "Release runtime grants failed",
        )
        require_success(
            self.compose("run", "--pull", "never", "--no-deps", "--rm", "-T", "schema"),
            "Release schema gate failed",
        )
        require_success(
            self.compose(
                "--profile", "seed", "run", "--pull", "never", "--no-deps", "--rm", "-T", "seed"
            ),
            "Release seed failed",
        )
        require_success(
            self.compose(
                "up",
                "-d",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "180",
                "api",
                "frontend",
                "gateway",
                "worker",
                timeout=240,
            ),
            "Release services failed",
        )
        self.started = True
        self.wait_gateway()
        self.require_worker_identity()
        self.wait_heartbeat()
        assert self.http("GET", "/login").status_code == 200
        for name in ("api", "worker", "frontend"):
            identifier = require_success(self.compose("ps", "-q", name), "Missing release service")
            record = json_output(
                self.compose("ps", "--format", "json", name), "Release service status failed"
            )
            assert record
            expected = self.release_spec.images[self.release_spec.services[name]]
            actual = command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{"id":{{json .Image}},"reference":{{json .Config.Image}}}',
                    identifier,
                ]
            )
            assert actual.strip() == canonical(
                {"id": expected.image_id, "reference": expected.reference}
            )


def consume(spec: Spec, scratch: Path, registry: Registry) -> None:
    require_configuration(spec.configuration)
    registry.verify_images(spec, pull=True)
    if "collector" in spec.images:
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                spec.images["collector"].reference,
                "--version",
            ]
        )
    stack = ReleaseStack(scratch, spec)
    try:
        stack.start()
    finally:
        stack.cleanup()
