from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import load_config
from .notify import DeliveryResult, NotificationEvent
from .receiver import ReceiverServer
from .runtime import MonitorRunner, RunSummary, build_runner
from .state import StateStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="offhost-probe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run the independent scheduled monitor")
    run.add_argument("--config", type=Path, required=True)
    status = subparsers.add_parser("status", help="print sanitized persisted monitor health")
    status.add_argument("--config", type=Path, required=True)
    receiver = subparsers.add_parser("receiver", help="run the loopback-only test receiver")
    receiver.add_argument("--port", type=int, default=8788)
    return parser


def _emit_summary(summary: RunSummary) -> None:
    checks = []
    for result in summary.results:
        state = summary.state.checks[result.check_name]
        checks.append(
            {
                "name": result.check_name,
                "outcome": result.outcome,
                "status_code": result.status_code,
                "firing": state.firing,
                "last_attempt_at": state.last_attempt_at,
                "last_evaluation_at": state.last_evaluation_at,
                "last_resolution_at": state.last_resolution_at,
            }
        )
    print(
        json.dumps(
            {
                "event": (
                    "monitor.capacity_limited" if summary.capacity_limited else "monitor.evaluated"
                ),
                "capacity_limited": summary.capacity_limited,
                "completed_at": summary.completed_at,
                "probe_health": summary.state.probe_health,
                "delivery_health": summary.state.delivery_health,
                "last_delivery_outcome": summary.state.last_delivery_outcome,
                "checks": checks,
                "transitions": summary.transitions,
                "pending_notifications": len(summary.state.pending),
                "last_delivery_attempt_at": summary.state.last_delivery_attempt_at,
                "last_delivery_success_at": summary.state.last_delivery_success_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _emit_delivery(
    event: NotificationEvent, result: DeliveryResult, attempted_at: str, completed_at: str
) -> None:
    attempted = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
    completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    occurred = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
    print(
        json.dumps(
            {
                "event": "monitor.notification_delivery",
                "event_id": event.event_id,
                "check": event.check_name,
                "status": "firing" if event.firing else "resolved",
                "outcome": result.outcome,
                "transition_at": event.occurred_at,
                "attempt_at": attempted_at,
                "completed_at": completed_at,
                "attempt_duration_ms": max(0, int((completed - attempted).total_seconds() * 1000)),
                "delivery_latency_ms": max(0, int((completed - occurred).total_seconds() * 1000)),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _status(config_path: Path) -> int:
    config = load_config(config_path)
    state = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    ).load()
    stale_budget = (
        config.interval_seconds + config.timeout_seconds + config.delivery_cycle_timeout_seconds
    )
    probe_stale = False
    if state.last_probe_completed_at is not None:
        completed_at = datetime.fromisoformat(state.last_probe_completed_at.replace("Z", "+00:00"))
        probe_stale = datetime.now(UTC) - completed_at > timedelta(seconds=stale_budget)
    probe_health = "degraded" if probe_stale else state.probe_health
    overall = (
        "degraded"
        if "degraded" in {probe_health, state.delivery_health}
        else "healthy" if probe_health == state.delivery_health == "healthy" else "unknown"
    )
    print(
        json.dumps(
            {
                "monitor_health": overall,
                "probe_health": probe_health,
                "probe_stale": probe_stale,
                "delivery_health": state.delivery_health,
                "last_delivery_outcome": state.last_delivery_outcome,
                "checks": {
                    name: {
                        "outcome": state.outcomes.get(name, "unknown"),
                        "status_code": state.status_codes.get(name),
                        "firing": check.firing,
                        "last_attempt_at": check.last_attempt_at,
                        "last_evaluation_at": check.last_evaluation_at,
                        "last_resolution_at": check.last_resolution_at,
                    }
                    for name, check in state.checks.items()
                },
                "timestamps": {
                    "probe_started_at": state.last_probe_started_at,
                    "probe_completed_at": state.last_probe_completed_at,
                    "evaluation_at": state.last_evaluation_at,
                    "delivery_attempt_at": state.last_delivery_attempt_at,
                    "delivery_success_at": state.last_delivery_success_at,
                    "resolution_at": state.last_resolution_at,
                },
                "counters": {
                    "probe_attempts": state.probe_attempts_total,
                    "evaluations": state.evaluations_total,
                    "delivery_attempts": state.delivery_attempts_total,
                },
                "pending_notifications": len(state.pending),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


async def _run_monitor(config_path: Path) -> None:
    runner: MonitorRunner = build_runner(load_config(config_path))
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    runner.on_summary = _emit_summary
    runner.on_delivery = _emit_delivery
    await runner.run_forever(stop)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            return _status(args.config)
        if args.command == "run":
            asyncio.run(_run_monitor(args.config))
            return 0
        if args.command == "receiver":
            if not 1 <= args.port <= 65535:
                raise ValueError("Receiver settings are invalid")
            with ReceiverServer(args.port) as server:
                print(
                    json.dumps(
                        {"event": "test_receiver.started", "host": "127.0.0.1", "port": args.port}
                    ),
                    flush=True,
                )
                server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    except ValueError:
        print(
            '{"event":"monitor.error","reason":"invalid_configuration_or_state"}', file=sys.stderr
        )
        return 2
    except OSError:
        print('{"event":"monitor.error","reason":"service_unavailable"}', file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
