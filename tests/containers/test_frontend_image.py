"""Real frontend image checks: RUN_CONTAINER_TESTS=1 python -m pytest tests/containers -q."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CONTAINER_TESTS") != "1",
    reason="Set RUN_CONTAINER_TESTS=1 for Docker checks",
)


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], cwd=ROOT, text=True, capture_output=True, timeout=600, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def frontend_image():
    tag = f"prl-frontend-test:{uuid.uuid4().hex}"
    docker("build", "--file", "Dockerfile.frontend", "--tag", tag, ".")
    try:
        yield tag
    finally:
        docker("image", "rm", "--force", tag)


def test_standalone_runtime_has_no_upstream_and_runs_unprivileged(frontend_image: str):
    config = json.loads(docker("image", "inspect", frontend_image))[0]["Config"]
    assert config["User"] == "node"
    assert not any(value.startswith("API_UPSTREAM_URL=") for value in config["Env"])
    docker(
        "run",
        "--rm",
        frontend_image,
        "node",
        "--input-type=module",
        "-e",
        """
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
assert.equal(process.getuid(), 1000);
const root = 'src/frontend/';
const manifest = JSON.parse(readFileSync(root + '.next/routes-manifest.json'));
assert.deepEqual(manifest.rewrites, { beforeFiles: [], afterFiles: [], fallback: [] });
assert.ok(existsSync(root + 'server.js'));
assert.ok(existsSync(root + 'public/branding/hus-logo.png'));
assert.ok(!existsSync(root + '.env.production'));
assert.ok(!existsSync('.git'));
""",
    )


def test_container_serves_api_mode_pages_and_standalone_assets(frontend_image: str):
    name = f"prl-frontend-test-{uuid.uuid4().hex}"
    docker("run", "--detach", "--name", name, "--publish", "127.0.0.1::3000", frontend_image)
    try:
        mapping = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]
        origin = f"http://127.0.0.1:{mapping['3000/tcp'][0]['HostPort']}"
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(origin + "/login", timeout=2) as response:
                    html = response.read().decode()
                    assert response.status == 200
                    assert response.headers.get("X-Powered-By") is None
                break
            except (URLError, ConnectionResetError, RemoteDisconnected):
                if time.monotonic() >= deadline:
                    pytest.fail("Frontend did not start: " + docker("logs", name))
                time.sleep(0.2)

        # A mock-mode build renders this banner; production must use the real API.
        assert "Bản demo dùng dữ liệu giả" not in html
        assets = re.findall(r'(?:src|href)="(/_next/static/[^"?]+)', html)
        assert any(asset.endswith(".js") for asset in assets)
        assert any(asset.endswith(".css") for asset in assets)
        for asset in set(assets) | {"/branding/hus-logo.png"}:
            with urlopen(origin + asset, timeout=5) as response:
                assert response.status == 200
                assert response.read()
    finally:
        docker("rm", "--force", name)
