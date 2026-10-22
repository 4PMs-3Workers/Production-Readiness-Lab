"""Real Docker checks: RUN_CONTAINER_TESTS=1 python -m pytest tests/containers -q.

Builds the production image and a disposable build-context probe. No database,
credentials, or host bind mounts are needed. Fails (never skips) on Docker errors
once explicitly enabled.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

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
def backend_image():
    tag = f"prl-backend-test:{uuid.uuid4().hex}"
    docker("build", "--file", "Dockerfile.backend", "--tag", tag, ".")
    try:
        yield tag
    finally:
        docker("image", "rm", "--force", tag)


def test_runtime_assets_and_unprivileged_commands(backend_image: str) -> None:
    config = json.loads(docker("image", "inspect", backend_image))[0]["Config"]
    assert config["User"] == "10001:10001"
    assert config["WorkingDir"] == "/app"
    assert config["Cmd"] == [
        "python",
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        "src/backend",
        "--no-proxy-headers",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    docker("run", "--rm", backend_image, "python", "-m", "pip", "check")
    docker(
        "run",
        "--rm",
        backend_image,
        "python",
        "-c",
        """
import importlib
import os
import runpy
import secrets
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.demo.data import load_dataset
assert os.getuid() == 10001
os.environ.update(
    DATABASE_URL="mysql+asyncmy://localhost/image_check",
    APPLICATION_ORIGIN="http://localhost:3000",
    COOKIE_SECRET=secrets.token_urlsafe(32),
    CSRF_SECRET=secrets.token_urlsafe(32),
    ACCEPTED_SCHEMA_REVISIONS=ScriptDirectory.from_config(Config("alembic.ini")).get_current_head(),
)
for module in ('app.main', 'app.worker.__main__', 'app.demo.loader', 'uvicorn'):
    importlib.import_module(module)
assert Path('alembic.ini').is_file()
assert ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
assert Path('src/backend/app/demo/dataset.json').is_file()
assert len(load_dataset().accounts) == 42
runpy.run_path('tools/scripts/seed_demo.py', run_name='image_asset_check')
""",
    )
    docker("run", "--rm", backend_image, "python", "-m", "app.worker", "--help")
    docker("run", "--rm", backend_image, "python", "-m", "alembic", "heads")


def test_shared_context_excludes_sensitive_and_generated_files(
    backend_image: str, tmp_path: Path
) -> None:
    # Let Docker itself interpret .dockerignore; do not duplicate its glob rules.
    excluded = [
        ".git/config",
        ".env",
        ".env.local",
        ".env.example",
        "src/frontend/.env.production",
        ".aws/credentials",
        ".ssh/id_rsa",
        ".npmrc",
        "credentials.json",
        "secrets.json",
        "server.key",
        "server.pem",
        ".venv/pyvenv.cfg",
        "venv/bin/python",
        "src/frontend/node_modules/sentinel",
        "node_modules/sentinel",
        "src/frontend/.next/server.js",
        "dist/bundle.js",
        "build/wheel.whl",
        "src/backend/app/__pycache__/x.pyc",
        ".pytest_cache/sentinel",
        ".mypy_cache/sentinel",
        ".ruff_cache/sentinel",
        ".cache/sentinel",
        ".coverage",
        "coverage.xml",
        "htmlcov/index.html",
        "coverage/report.json",
        "playwright-report/index.html",
        "test-results/results.xml",
        ".superpowers/sentinel",
        ".agents/sentinel",
        ".codex/sentinel",
    ]
    included = [
        "requirements.txt",
        "pyproject.toml",
        "alembic.ini",
        "package-lock.json",
        "src/backend/app/main.py",
        "src/backend/app/demo/dataset.json",
        "src/backend/migrations/env.py",
        "tools/scripts/seed_demo.py",
        "src/frontend/package.json",
        "src/frontend/src/app/page.tsx",
    ]
    shutil.copyfile(ROOT / ".dockerignore", tmp_path / ".dockerignore")
    for name in excluded + included:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        f"FROM {backend_image}\nCOPY . /context\n", encoding="utf-8"
    )
    tag = f"prl-context-test:{uuid.uuid4().hex}"
    docker("build", "--tag", tag, str(tmp_path))
    try:
        script = (
            'from pathlib import Path; root=Path("/context"); '
            f"assert not [p for p in {excluded!r} if (root/p).exists()]; "
            f"assert all((root/p).is_file() for p in {included!r})"
        )
        docker("run", "--rm", tag, "python", "-c", script)
    finally:
        docker("image", "rm", "--force", tag)
