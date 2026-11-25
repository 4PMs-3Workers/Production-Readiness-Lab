"""Owned loopback API process for separating load-generator scheduling from ASGI."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx


@asynccontextmanager
async def native_api(settings):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    values = settings.model_dump(mode="json", by_alias=True)
    values.update(
        COOKIE_SECRET=settings.cookie_secret.get_secret_value(),
        CSRF_SECRET=settings.csrf_secret.get_secret_value(),
    )
    environment.update({key: str(value) for key, value in values.items() if value is not None})
    environment["PYTHONPATH"] = str(root / "src/backend")
    origin = f"http://127.0.0.1:{port}"
    # No command-line secrets, output bodies, or credential-bearing error logs.
    diagnostic = os.getenv("NFR03_SERVER_DIAGNOSTICS") == "1"
    output = (
        (root / "build/nfr03" / ("server-" + uuid4().hex + ".jsonl")).open("w", encoding="utf-8")
        if diagnostic
        else None
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            (
                "tests.backend.integration.nfr03_native:diagnostic_app"
                if diagnostic
                else "app.factory:create_app"
            ),
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
            "--no-proxy-headers",
        ],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=output if output else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Owned NFR API exited during startup")
                try:
                    response = await client.get(origin + "/health/ready")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Owned NFR API did not become live")
        yield origin
    finally:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=5)
        if output is not None:
            output.close()


def diagnostic_app():
    import pytest
    from tests.backend.integration.test_appointment_export_nfr03 import (
        SAMPLE,
        STAGE,
        install_profiling,
    )
    from tools.load.export_probe import measure_pool

    from app.factory import create_app

    app = create_app()
    original = app.router.lifespan_context
    patch = pytest.MonkeyPatch()
    install_profiling(patch)

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original(application):
                with measure_pool(application.state.engine, SAMPLE, STAGE):
                    yield
        finally:
            patch.undo()

    app.router.lifespan_context = lifespan

    class Probe:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            record = {"event": "nfr03.server", "sample": uuid4().hex, "server_failures": []}
            token = SAMPLE.set(record)

            async def capture(message):
                if message["type"] == "http.response.start":
                    record["status"] = message["status"]
                await send(message)

            try:
                await app(scope, receive, capture)
            finally:
                record.pop("_acquire_started", None)
                record["request_id"] = scope.get("state", {}).get("request_id")
                print(json.dumps(record), flush=True)
                SAMPLE.reset(token)

    return Probe()
