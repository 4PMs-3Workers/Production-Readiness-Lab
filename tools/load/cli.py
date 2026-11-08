"""Command line entrypoint for preparing, running, and cleaning isolated load schemas."""

import argparse
import asyncio
import os
import re
from pathlib import Path

from tools.load.config import LoadConfig, LoadSafetyError, validate_load_config
from tools.load.fixtures import (
    create_owned_schema,
    drop_owned_schema,
    verify_owned_schema,
)
from tools.load.gateway import (
    collect_gateway_window,
    collect_runtime_signals,
    confirm_candidate_release,
    verify_prometheus_endpoint,
)
from tools.load.invariants import verify_post_run_invariants
from tools.load.metrics import summarize_gateway_events
from tools.load.report import (
    build_report,
    collect_provenance,
    sample_generator_host,
    write_report,
)
from tools.load.runner import (
    confirm_fixture_target,
    load_workload_fixture,
    run_workload,
)

_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Reproducible isolated booking load lab")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--target-url", required=True)
        command.add_argument("--release-sha", required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--environment", choices=("demo", "local"), default="demo")
        command.add_argument("--seed", type=int, default=53)
        command.add_argument("--duration-seconds", type=int, default=600)
        command.add_argument("--report", type=Path, default=Path("artifacts/load-report.json"))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    run_id = _validate_run_id(args.run_id)
    database_url = os.environ.get("PRL_LOAD_DATABASE_URL", "")
    if not database_url:
        raise LoadSafetyError("PRL_LOAD_DATABASE_URL must target the dedicated load schema")
    config = LoadConfig(
        target_url=args.target_url,
        release_sha=args.release_sha,
        database_url=database_url,
        opt_in=os.environ.get("PRL_LOAD_TEST_OPT_IN", ""),
        duration_seconds=args.duration_seconds,
        sessions=20,
    )
    validate_load_config(config)
    if config.schema_name != f"prl_load_{run_id}":
        raise LoadSafetyError("PRL_LOAD_DATABASE_URL schema must match --run-id")
    if args.command in {"prepare", "run"} and not os.environ.get("PRL_LOAD_LOKI_URL"):
        raise LoadSafetyError("PRL_LOAD_LOKI_URL is required to verify candidate release telemetry")
    if args.command == "run" and not os.environ.get("PRL_LOAD_PROMETHEUS_URL"):
        raise LoadSafetyError("PRL_LOAD_PROMETHEUS_URL is required for runtime signal evidence")
    if (
        args.command in {"prepare", "run"}
        and len(os.environ.get("PRL_LOAD_FIXTURE_PASSWORD", "")) < 32
    ):
        raise LoadSafetyError(
            "PRL_LOAD_FIXTURE_PASSWORD must be a private per-run secret of 32+ chars"
        )
    if args.command == "prepare":
        return asyncio.run(_prepare(args, config))
    if args.command == "cleanup":
        return asyncio.run(_cleanup(config, run_id))
    return asyncio.run(_run(args, config, run_id))


async def _prepare(args: argparse.Namespace, config: LoadConfig) -> int:
    _require_matching_checkout(config.release_sha)
    await _confirm_release(args, config)
    await create_owned_schema(
        config, seed=args.seed, password=os.environ["PRL_LOAD_FIXTURE_PASSWORD"]
    )
    print(f"Prepared owned schema {config.schema_name} for release {config.release_sha}.")
    print(
        "Configure the candidate API to use this schema, then run the canary-backed load command."
    )
    return 0


async def _run(args: argparse.Namespace, config: LoadConfig, run_id: str) -> int:
    _require_matching_checkout(config.release_sha)
    provenance = collect_provenance(_repository_root(), target_url=config.target_url)
    await _confirm_release(args, config)
    prometheus_url = os.environ["PRL_LOAD_PROMETHEUS_URL"]
    await verify_prometheus_endpoint(prometheus_url)
    dataset = await verify_owned_schema(config, run_id=run_id)
    fixture = load_workload_fixture(args.seed, run_id, os.environ["PRL_LOAD_FIXTURE_PASSWORD"])
    await confirm_fixture_target(config.target_url, fixture)
    host_start = sample_generator_host()
    workload = await run_workload(config, fixture, seed=args.seed)
    host_end = sample_generator_host()
    loki_url = os.environ.get("PRL_LOAD_LOKI_URL", "")
    await asyncio.sleep(5)
    gateway = await collect_gateway_window(
        loki_url,
        config.release_sha,
        workload.started_at,
        workload.ended_at,
        environment=args.environment,
        expected_records=workload.expected_gateway_requests,
        expected_request_ids=workload.gateway_request_ids,
    )
    sli = summarize_gateway_events(
        gateway.events,
        duration_seconds=workload.duration_seconds,
        telemetry_complete=gateway.complete,
        generator_saturated=workload.generator_saturated,
    )
    runtime_signals = await collect_runtime_signals(
        prometheus_url, int(workload.duration_seconds) + 15, environment=args.environment
    )
    runtime_signals_complete = _runtime_signals_complete(runtime_signals)
    invariants = await verify_post_run_invariants(
        config.database_url, workload, expected_release_sha=config.release_sha
    )
    payload = build_report(
        target_url=config.target_url,
        environment=args.environment,
        candidate_release_sha=config.release_sha,
        provenance=provenance,
        dataset=dataset,
        workload=workload,
        sli=sli,
        runtime_signals=runtime_signals,
        host_start=host_start,
        host_end=host_end,
        invariant_results=invariants,
    )
    payload["gateway_sli_collection"] = {
        "complete": gateway.complete,
        "records": gateway.returned_records,
        "expected_records": workload.expected_gateway_requests,
        "record_count_matches_generator": (
            gateway.returned_records == workload.expected_gateway_requests
        ),
        "truncated": gateway.truncated,
        "reason": gateway.reason,
    }
    payload["owned_resources"] = {
        "database_schema": config.schema_name,
        "run_id": run_id,
        "cleanup": "retained_until_explicit_cleanup_command",
    }
    sli_achieved = sli.availability.meets_target is True and sli.fast_success.meets_target is True
    sli_evidence_complete = (
        gateway.complete
        and not workload.generator_saturated
        and sli.availability.status == "measured"
        and sli.fast_success.status == "measured"
    )
    evidence_complete = sli_evidence_complete and runtime_signals_complete
    local_manifest = provenance.get("local_deployment_manifest")
    target_artifact_identity_complete = (
        isinstance(local_manifest, dict)
        and local_manifest.get("target_artifact_identity_complete") is True
    )
    payload["acceptance"] = {
        "local_deployment_manifest_matches_release": (
            isinstance(local_manifest, dict) and local_manifest.get("manifest_complete") is True
        ),
        "target_artifact_identity_complete": target_artifact_identity_complete,
        "availability_target_achieved_in_window": sli.availability.meets_target,
        "fast_success_target_achieved_in_window": sli.fast_success.meets_target,
        "sli_evidence_complete": sli_evidence_complete,
        "runtime_signals_complete": runtime_signals_complete,
        "post_run_invariants_passed": bool(invariants["passed"]),
        "seven_day_slo_claimed": False,
    }
    write_report(args.report, payload)
    print(f"Wrote sanitized report to {args.report}.")
    print(
        "Availability: "
        f"{_ratio_text(sli.availability.ratio)}; fast-success: "
        f"{_ratio_text(sli.fast_success.ratio)}; invariants: "
        f"{'passed' if invariants['passed'] else 'failed'}."
    )
    return (
        0
        if invariants["passed"]
        and evidence_complete
        and sli_achieved
        and target_artifact_identity_complete
        else 2
    )


async def _cleanup(config: LoadConfig, run_id: str) -> int:
    await drop_owned_schema(config, run_id=run_id)
    print(f"Removed verified owned schema {config.schema_name}.")
    return 0


async def _confirm_release(args: argparse.Namespace, config: LoadConfig) -> None:
    loki_url = os.environ.get("PRL_LOAD_LOKI_URL", "")
    if not loki_url:
        raise LoadSafetyError("PRL_LOAD_LOKI_URL is required to verify candidate release telemetry")
    await confirm_candidate_release(
        config.target_url,
        loki_url,
        config.release_sha,
        environment=args.environment,
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_matching_checkout(release_sha: str) -> None:
    provenance = collect_provenance(_repository_root())
    if (
        provenance["checkout_source_sha"] != release_sha
        or provenance["checkout_has_uncommitted_changes"]
    ):
        raise LoadSafetyError(
            "Local checkout must be clean and HEAD must match --release-sha "
            "so fixture migrations match the candidate"
        )


def _validate_run_id(value: str) -> str:
    if _RUN_ID.fullmatch(value) is None:
        raise LoadSafetyError("--run-id must be a 32-character lowercase UUID hex string")
    return value


def _ratio_text(value: float | None) -> str:
    return "insufficient evidence" if value is None else f"{value:.3%}"


def _runtime_signals_complete(runtime_signals: dict[str, object]) -> bool:
    signals = runtime_signals.get("signals")
    required = {
        "api_scrape_up",
        "worker_telemetry_scrape_up",
        "worker_backlog_max",
        "api_pool_checked_out_max",
        "api_pool_overflow_max",
        "lock_timeouts",
        "pool_timeouts",
    }
    return (
        isinstance(signals, dict)
        and required.issubset(signals)
        and all(
            isinstance(signals[key], dict)
            and signals[key].get("status") == "measured"
            and (
                key not in {"api_scrape_up", "worker_telemetry_scrape_up"}
                or signals[key].get("value") == 1
            )
            for key in required
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
