"""Sanitized, size-bounded run reports and local provenance/resource measurements."""

import hashlib
import json
import math
import os
import platform
import re
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from tools.deploy.evidence import validate as validate_deployment_evidence
from tools.load.gateway import safe_service_url
from tools.load.metrics import SliSummary
from tools.load.runner import WorkloadResult

MAX_REPORT_BYTES = 256 * 1024
PROVENANCE_FILES = (
    "tools/deploy/compose.local.yaml",
    "tools/deploy/compose.observability.yaml",
    "tools/deploy/nginx/nginx.conf",
    "tools/infra/otel-collector.yaml",
    "tools/infra/prometheus.yml",
    "tools/infra/images.lock.json",
)


def collect_provenance(root: Path) -> dict[str, object]:
    """Read source identity and hashes for the load/runtime configuration in this checkout."""
    source = _git(root, "rev-parse", "HEAD")
    dirty = bool(_git(root, "status", "--porcelain"))
    digests = {}
    for relative in PROVENANCE_FILES:
        path = root / relative
        digests[relative] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
    image_digests = _locked_image_digests(root / "tools/infra/images.lock.json")
    local_manifest = _deployed_artifact_identity(root, source)
    return {
        "checkout_source_sha": source,
        "checkout_has_uncommitted_changes": dirty,
        "configuration_sha256": digests,
        "locked_image_digests": image_digests,
        "local_deployment_manifest": local_manifest,
    }


def _deployed_artifact_identity(root: Path, source_sha: str) -> dict[str, object]:
    manifest = root / "build/deploy" / source_sha / "manifest.json"
    unavailable: dict[str, object] = {
        "status": "unavailable",
        "manifest_complete": False,
        "target_artifact_identity_complete": False,
        "target_association": "not_attested_to_target_url",
        "image_ids": {},
        "image_digests": {},
        "configuration_sha256": None,
        "manifest_sha256": None,
    }
    try:
        encoded = manifest.read_bytes()
        value = json.loads(encoded)
    except (OSError, json.JSONDecodeError):
        return unavailable
    if len(encoded) > MAX_REPORT_BYTES:
        return unavailable
    try:
        value = validate_deployment_evidence(value)
    except (ValueError, TypeError, KeyError):
        return unavailable
    verification = value.get("verification")
    if not isinstance(verification, dict):
        return unavailable
    if (
        value.get("manifest_version") != 2
        or value.get("source_sha") != source_sha
        or verification["command"] != "evidence"
        or verification.get("status") != "passed"
        or value.get("deployment_state") != "active"
    ):
        return unavailable
    images = value.get("images")
    configuration_sha = value.get("configuration_sha256")
    if not isinstance(images, dict) or not isinstance(configuration_sha, str):
        return unavailable
    digest_pattern = re.compile(r"sha256:[0-9a-f]{64}\Z")
    image_ids: dict[str, str] = {}
    image_digests: dict[str, list[str]] = {}
    for name, image in images.items():
        if not isinstance(name, str) or not isinstance(image, dict):
            continue
        image_id = image.get("id")
        digests = image.get("digests")
        if (
            isinstance(image_id, str)
            and digest_pattern.fullmatch(image_id) is not None
            and isinstance(digests, list)
            and all(
                isinstance(digest, str)
                and "@sha256:" in digest
                and digest_pattern.fullmatch(digest.rsplit("@", 1)[-1]) is not None
                for digest in digests
            )
        ):
            image_ids[name[:64]] = image_id
            image_digests[name[:64]] = sorted(digests)
    required_images = {"api", "frontend", "worker", "gateway"}
    manifest_complete = (
        re.fullmatch(r"[0-9a-f]{64}", configuration_sha) is not None
        and required_images <= image_ids.keys()
        and required_images <= image_digests.keys()
    )
    if not manifest_complete:
        return unavailable
    return {
        "status": "local_manifest_matches_source",
        "manifest_complete": True,
        "target_artifact_identity_complete": False,
        "target_association": "not_attested_to_target_url",
        "image_ids": dict(sorted(image_ids.items())),
        "image_digests": dict(sorted(image_digests.items())),
        "configuration_sha256": configuration_sha,
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def sample_generator_host() -> dict[str, object]:
    """Capture only aggregate local generator host data; target-host metrics are separate."""
    sample: dict[str, object] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "platform": platform.system(),
        "cpu_count": os.cpu_count(),
        "process_cpu_seconds": sum(os.times()[:2]),
        "max_resident_memory_bytes": None,
        "load_average_1m": None,
    }
    try:
        import importlib

        resource_module = importlib.import_module("resource")
        maximum_rss = resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss
        sample["max_resident_memory_bytes"] = int(maximum_rss * (1024 if os.name != "nt" else 1))
    except (ImportError, OSError, ValueError):
        pass
    try:
        get_load = getattr(os, "getloadavg", None)
        if get_load is not None:
            sample["load_average_1m"] = get_load()[0]
    except (AttributeError, OSError):
        pass
    return sample


def build_report(
    *,
    target_url: str,
    environment: str,
    candidate_release_sha: str,
    provenance: dict[str, object],
    dataset: dict[str, int],
    workload: WorkloadResult,
    sli: SliSummary,
    runtime_signals: dict[str, object],
    host_start: dict[str, object],
    host_end: dict[str, object],
    invariant_results: dict[str, object],
) -> dict[str, object]:
    elapsed = max(0.001, (workload.ended_at - workload.started_at).total_seconds())
    lags = sorted(workload.event_loop_lag_seconds)
    operations = dict(sorted(workload.operation_counts.items()))
    payload: dict[str, object] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "candidate_release_sha": candidate_release_sha,
            **provenance,
        },
        "target": {
            "origin": safe_service_url(target_url),
            "environment": environment,
        },
        "dataset": dataset,
        "workload": {
            "started_at": workload.started_at.isoformat(),
            "ended_at": workload.ended_at.isoformat(),
            "window_seconds": workload.duration_seconds,
            "requested_concurrent_sessions": workload.requested_sessions,
            "authenticated_sessions": workload.authenticated_sessions,
            "operation_mix": {
                "student": {"read": 0.5, "book": 0.25, "cancel": 0.25},
                "staff": {"read": 0.5, "proposal": 0.5},
            },
            "operation_counts": operations,
            "expected_gateway_requests": workload.expected_gateway_requests,
            "operations_per_second": sum(
                count for key, count in operations.items() if key.endswith(".attempted")
            )
            / elapsed,
            "event_loop_lag_seconds": {
                "sample_count": len(lags),
                "p95": _percentile(lags, 0.95),
                "max": lags[-1] if lags else None,
            },
            "generator_saturated": workload.generator_saturated,
        },
        "gateway_sli": asdict(sli),
        "runtime_signals": runtime_signals,
        "generator_host": {"start": host_start, "end": host_end},
        "target_host_resources": {"status": "not_exposed_by_candidate_telemetry"},
        "invariants": invariant_results,
        "interpretation": {
            "seven_day_slo_claimed": False,
            "availability_target": "99.5% in this observed test window",
            "fast_success_target": "95% of successful requests <= 500 ms",
        },
    }
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Sanitized load report exceeds the 256 KiB bound")
    return payload


def write_report(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Sanitized load report exceeds the 256 KiB bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Unable to capture source provenance") from error
    return result.stdout.strip()


def _locked_image_digests(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    values = payload.get("images", payload.get("vendor_images", {}))
    if not isinstance(values, dict):
        return {}
    digests = {}
    for name, image in values.items():
        if isinstance(image, dict):
            image = image.get("reference") or image.get("image")
        if isinstance(name, str) and isinstance(image, str) and "@sha256:" in image:
            digests[name[:64]] = image.rsplit("@", 1)[-1][:80]
    return dict(sorted(digests.items()))


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    return values[max(0, math.ceil(quantile * len(values)) - 1)]
