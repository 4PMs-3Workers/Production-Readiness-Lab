"""Deploy immutable reviewed manifests or restore the retained compatible release."""

from __future__ import annotations

import argparse
import os
import ssl
from pathlib import Path
from typing import Literal

from tools.deploy import demo
from tools.deploy.evidence import lifecycle_directory
from tools.deploy.release_images import Registry, command
from tools.deploy.release_manifest import ROOT, canonical
from tools.deploy.release_trust import require_clean_source
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_evidence import Result, result
from tools.deploy.rollout_http import SmokeSession, credentials
from tools.deploy.rollout_readiness import ready
from tools.deploy.rollout_runtime import ReleaseRuntime
from tools.deploy.rollout_smoke import business_smoke
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RolloutError("invalid-manifest")


def parser() -> Parser:
    root = Parser(description=__doc__)
    root.add_argument("--plain-http", action="store_true", help="Loopback registry rehearsal only")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("deploy", "rollback"):
        selected = commands.add_parser(name)
        selected.add_argument("--overlay", choices=["local", "https"], required=True)
        selected.add_argument("--observability", action="store_true")
        if name == "deploy":
            selected.add_argument("reference")
            selected.add_argument("--resume", action="store_true")
    commands.add_parser("status")
    return root


def controller(args, store: StateStore) -> RolloutController:
    try:
        source = command(["git", "rev-parse", "HEAD"], timeout=5).decode().strip()
        require_clean_source(source)
        host = demo.Demo(args.overlay, source, observability=args.observability)
        with host._compose_environment():
            environment = dict(os.environ)
        account = credentials(Path(os.environ["PRL_SMOKE_CREDENTIALS_FILE"]))
        context = None
        if os.environ.get("PRL_SMOKE_CA_FILE"):
            path = Path(os.environ["PRL_SMOKE_CA_FILE"])
            if not path.is_absolute() or not path.is_file():
                raise RolloutError("incompatible-configuration")
            context = ssl.create_default_context(cafile=str(path))
        runtime = ReleaseRuntime(
            host.base,
            environment,
            host.origin,
            "prl-demo",
            store.directory / "rendered",
            overlay=args.overlay,
            observability=args.observability,
        )

        def smoke(selected: ReleaseRuntime) -> list[str]:
            with SmokeSession(selected.origin, selected.deadline, tls=context or True) as session:
                return business_smoke(session, account, selected.effects)

        return RolloutController(
            store,
            Registry(plain_http=args.plain_http),
            runtime,
            source,
            host.configuration_sha256,
            smoke=smoke,
            readiness=lambda selected: ready(selected, context),
        )
    except RolloutError:
        raise
    except Exception:
        raise RolloutError("incompatible-configuration") from None


def main(argv: list[str] | None = None) -> int:
    operation: Literal["deploy", "rollback", "status"] = "status"
    try:
        args = parser().parse_args(argv)
        operation = args.command
        store = StateStore(lifecycle_directory(ROOT))
        if operation == "status":
            report = result(operation, store.load())
        else:
            host = controller(args, store)
            state = (
                host.deploy(args.reference, resume=args.resume)
                if operation == "deploy"
                else host.rollback()
            )
            report = result(operation, state)
        print(canonical(report).decode())
        if operation == "status":
            return 0
        return (
            0
            if (operation, report.outcome) in {("deploy", "succeeded"), ("rollback", "rolled_back")}
            else 1
        )
    except RolloutError as error:
        print(canonical(Result(command=operation, outcome="failed", reason=error.reason)).decode())
        return 1
    except Exception:
        print(
            canonical(
                Result(command=operation, outcome="failed", reason="unexpected-failure")
            ).decode()
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
