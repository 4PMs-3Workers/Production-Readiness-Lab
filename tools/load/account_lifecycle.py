"""Run a guarded 120-account soft-delete/restore HTTP concurrency check.

The target inventory contains disposable account IDs and one distinct ADMIN
session/CSRF credential per task. Keep that inventory outside version control.
"""

import argparse
import asyncio
import json
import math
import os
import time
from pathlib import Path
from statistics import mean
from urllib.parse import urlsplit
from uuid import UUID

import httpx

_OPT_IN = "I_UNDERSTAND_THIS_MUTATES_120_DEDICATED_TEST_ACCOUNTS"
_TARGET_COUNT = 120
_SLA_MS = 3500.0


class LifecycleLoadError(ValueError):
    """The lifecycle load run failed a preflight or acceptance check."""


def percentile(values: list[float], quantile: float) -> float:
    """Return the nearest-rank percentile in milliseconds."""
    if not values:
        raise ValueError("values must not be empty")
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def validate_target_inventory(rows: object) -> list[dict[str, str]]:
    """Require 120 distinct targets and 120 distinct actor sessions."""
    if not isinstance(rows, list) or len(rows) < _TARGET_COUNT:
        raise LifecycleLoadError("The fixture must contain at least 120 account targets")
    selected = rows[:_TARGET_COUNT]
    required = {
        "id",
        "expected_updated_at",
        "session_cookie",
        "csrf_cookie",
        "csrf_token",
    }
    targets: list[dict[str, str]] = []
    account_ids: set[str] = set()
    sessions: set[str] = set()
    for index, row in enumerate(selected):
        if not isinstance(row, dict) or not required.issubset(row):
            raise LifecycleLoadError(f"Target row {index + 1} is missing required fields")
        account_id = str(UUID(str(row["id"])))
        updated_at = str(row["expected_updated_at"])
        session_cookie = str(row["session_cookie"])
        csrf_cookie = str(row["csrf_cookie"])
        csrf_token = str(row["csrf_token"])
        if not all((updated_at, session_cookie, csrf_cookie, csrf_token)):
            raise LifecycleLoadError(f"Target row {index + 1} contains a blank value")
        if account_id in account_ids:
            raise LifecycleLoadError("Target account IDs must be unique")
        if session_cookie in sessions:
            raise LifecycleLoadError("Each concurrent task must use a distinct admin session")
        account_ids.add(account_id)
        sessions.add(session_cookie)
        targets.append(
            {
                "id": account_id,
                "expected_updated_at": updated_at,
                "session_cookie": session_cookie,
                "csrf_cookie": csrf_cookie,
                "csrf_token": csrf_token,
            }
        )
    return targets


def validate_origin(target_url: str, origin: str) -> str:
    """Reject credentials, path/query injection, and plain-HTTP remote targets."""
    target = urlsplit(target_url)
    parsed_origin = urlsplit(origin)
    if (
        target.scheme not in {"http", "https"}
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        raise LifecycleLoadError("Target URL must be an origin without credentials or paths")
    if target.scheme != "https" and target.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise LifecycleLoadError("Plain HTTP is allowed only for a loopback target")
    expected = f"{target.scheme}://{target.netloc}"
    supplied = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
    if supplied != expected or parsed_origin.path not in {"", "/"} or parsed_origin.query:
        raise LifecycleLoadError("Origin must exactly match the target origin")
    return expected


async def run(target_url: str, origin: str, targets: list[dict[str, str]]) -> dict[str, object]:
    """Run 120 paired soft-delete/restore tasks with 120 concurrent admin sessions."""
    base = validate_origin(target_url, origin)
    limits = httpx.Limits(max_connections=_TARGET_COUNT, max_keepalive_connections=_TARGET_COUNT)
    timeout = httpx.Timeout(15.0, connect=5.0)
    delete_ms: list[float] = []
    restore_ms: list[float] = []
    failures = 0
    semaphore = asyncio.Semaphore(_TARGET_COUNT)

    async with httpx.AsyncClient(
        base_url=base,
        limits=limits,
        timeout=timeout,
        follow_redirects=False,
    ) as client:

        async def run_one(target: dict[str, str]) -> None:
            nonlocal failures
            async with semaphore:
                account_id = target["id"]
                headers = {
                    "Origin": origin,
                    "X-CSRF-Token": target["csrf_token"],
                }
                cookies = {
                    "prl_session": target["session_cookie"],
                    "prl_csrf": target["csrf_cookie"],
                }
                start = time.perf_counter()
                try:
                    deleted = await client.request(
                        "DELETE",
                        f"/api/v1/admin/accounts/{account_id}",
                        json={"expected_updated_at": target["expected_updated_at"]},
                        headers=headers,
                        cookies=cookies,
                    )
                except httpx.HTTPError:
                    failures += 1
                    return
                delete_ms.append((time.perf_counter() - start) * 1000)
                if deleted.status_code != 200:
                    failures += 1
                    return
                try:
                    deleted_body = deleted.json()
                    deleted_version = deleted_body["updated_at"]
                    if deleted_body["deleted_at"] is None or deleted_body["is_active"] is not False:
                        failures += 1
                        return
                except (KeyError, ValueError, TypeError):
                    failures += 1
                    return

                start = time.perf_counter()
                try:
                    restored = await client.post(
                        f"/api/v1/admin/accounts/{account_id}/restore",
                        json={"expected_updated_at": deleted_version},
                        headers=headers,
                        cookies=cookies,
                    )
                except httpx.HTTPError:
                    failures += 1
                    return
                restore_ms.append((time.perf_counter() - start) * 1000)
                if restored.status_code != 200:
                    failures += 1
                    return
                try:
                    restored_body = restored.json()
                    if restored_body["deleted_at"] is not None:
                        failures += 1
                    if restored_body["is_active"] is not True:
                        failures += 1
                except (KeyError, ValueError, TypeError):
                    failures += 1

        started = time.perf_counter()
        await asyncio.gather(*(run_one(target) for target in targets))
        elapsed = time.perf_counter() - started

    delete_p95 = percentile(delete_ms, 0.95) if delete_ms else None
    restore_p95 = percentile(restore_ms, 0.95) if restore_ms else None
    return {
        "target_accounts": len(targets),
        "concurrent_tasks": _TARGET_COUNT,
        "completed_delete_requests": len(delete_ms),
        "completed_restore_requests": len(restore_ms),
        "failures": failures,
        "elapsed_seconds": round(elapsed, 3),
        "delete_p95_ms": round(delete_p95, 2) if delete_p95 is not None else None,
        "restore_p95_ms": round(restore_p95, 2) if restore_p95 is not None else None,
        "delete_mean_ms": round(mean(delete_ms), 2) if delete_ms else None,
        "restore_mean_ms": round(mean(restore_ms), 2) if restore_ms else None,
        "p95_target_ms": _SLA_MS,
        "meets_p95_target": (
            failures == 0
            and delete_p95 is not None
            and restore_p95 is not None
            and delete_p95 <= _SLA_MS
            and restore_p95 <= _SLA_MS
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="120-task account soft-delete/restore load check")
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument(
        "--targets", type=Path, required=True, help="Private JSON fixture; never commit it"
    )
    parser.add_argument(
        "--report", type=Path, default=Path("artifacts/account-lifecycle-load.json")
    )
    args = parser.parse_args()
    if os.environ.get("PRL_ACCOUNT_LOAD_OPT_IN") != _OPT_IN:
        raise LifecycleLoadError("Explicit disposable-account load-test opt-in is required")
    rows = json.loads(args.targets.read_text(encoding="utf-8"))
    targets = validate_target_inventory(rows)
    result = asyncio.run(run(args.target_url, args.origin, targets))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["meets_p95_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
