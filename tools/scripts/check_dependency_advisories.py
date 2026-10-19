#!/usr/bin/env python3
"""Fail-closed policy gate for npm and Python dependency advisory reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_IDENTIFIER_RE = re.compile(r"\b(?:GHSA-[0-9A-Z-]+|PYSEC-\d{4}-\d+|CVE-\d{4}-\d+)\b", re.I)
_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^;\s]+)(?:\s*;.*)?$")
_EXCEPTION_FIELDS = {
    "ecosystem",
    "advisory",
    "package",
    "version",
    "scope",
    "owner",
    "expires_at",
    "evidence",
}
_REQUIRED_NPM_DEPENDENCY_COUNTS = ("prod", "dev", "optional", "peer", "peerOptional", "total")
_REQUIRED_NPM_SEVERITY_COUNTS = ("info", "low", "moderate", "high", "critical", "total")


class InputError(ValueError):
    """Raised when a collector report, lock, or exception policy is incomplete."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"{label} must be a JSON object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} must be a non-empty string")
    return value.strip()


def _version(value: Any, label: str) -> str:
    version = _string(value, label)
    if any(character in version for character in "*<>=!~,"):
        raise InputError(f"{label} must be an exact version, not a range")
    return version


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _identifiers(value: Any) -> set[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        candidates = []
    return {
        match.upper() for candidate in candidates for match in _IDENTIFIER_RE.findall(candidate)
    }


def _json_file(path: Path, label: str) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            return _object(json.load(source), label)
    except FileNotFoundError as error:
        raise InputError(f"{label} is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise InputError(f"{label} is malformed JSON: {error}") from error
    except OSError as error:
        raise InputError(f"{label} could not be read: {error}") from error


def _npm_lock_packages(lock: Mapping[str, Any]) -> Mapping[str, Any]:
    if lock.get("lockfileVersion") not in (2, 3):
        raise InputError("npm lock must use package-lock version 2 or 3")
    packages = _object(lock.get("packages"), "npm lock packages")
    if not isinstance(packages.get(""), Mapping):
        raise InputError("npm lock is incomplete: packages must contain the root entry")
    return packages


def _npm_dependency_map(package: Mapping[str, Any]) -> dict[str, Any]:
    dependencies: dict[str, Any] = {}
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        value = package.get(field, {})
        if not isinstance(value, Mapping):
            raise InputError(f"npm lock dependency field {field!r} must be an object")
        dependencies.update(value)
    return dependencies


def _npm_ancestors(package_path: str) -> list[str]:
    ancestors: list[str] = []
    current = package_path
    while current:
        ancestors.append(current)
        if "/node_modules/" in current:
            current = current.rsplit("/node_modules/", 1)[0]
        elif current.startswith("node_modules/"):
            current = ""
        elif "/" in current:
            current = current.rsplit("/", 1)[0]
        else:
            current = ""
    ancestors.append("")
    return ancestors


def _npm_resolve_node(packages: Mapping[str, Any], parent_path: str, name: str) -> str | None:
    for ancestor in _npm_ancestors(parent_path):
        candidate = f"{ancestor}/node_modules/{name}" if ancestor else f"node_modules/{name}"
        if candidate in packages:
            return candidate
    return None


def _npm_reachable_nodes(packages: Mapping[str, Any]) -> set[str]:
    root = packages[""]
    assert isinstance(root, Mapping)
    pending = [""]
    workspaces = root.get("workspaces", [])
    if not isinstance(workspaces, list) or any(not isinstance(item, str) for item in workspaces):
        raise InputError("npm lock root workspaces must be an array of workspace paths")
    for workspace in workspaces:
        if workspace not in packages:
            raise InputError(f"npm workspace {workspace!r} is absent from package-lock.json")
        pending.append(workspace)
    reachable: set[str] = set()
    while pending:
        package_path = pending.pop()
        if package_path in reachable:
            continue
        package = packages.get(package_path)
        if not isinstance(package, Mapping):
            raise InputError(f"npm dependency graph references missing lock entry {package_path!r}")
        reachable.add(package_path)
        if package.get("link") is True:
            target = package.get("resolved")
            if not isinstance(target, str) or target not in packages:
                raise InputError(f"npm lock link {package_path!r} has no valid workspace target")
            pending.append(target)
            continue
        for dependency_name in _npm_dependency_map(package):
            resolved = _npm_resolve_node(packages, package_path, dependency_name)
            if resolved is not None:
                pending.append(resolved)
    return reachable


def _npm_report(report: Mapping[str, Any], packages: Mapping[str, Any]) -> list[dict[str, Any]]:
    if report.get("auditReportVersion") != 2:
        raise InputError("npm audit report is incomplete: auditReportVersion must be 2")
    metadata = _object(report.get("metadata"), "npm audit metadata")
    dependency_counts = _object(metadata.get("dependencies"), "npm dependency counts")
    severity_counts = _object(metadata.get("vulnerabilities"), "npm vulnerability counts")
    for key in _REQUIRED_NPM_DEPENDENCY_COUNTS:
        value = dependency_counts.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InputError(f"npm dependency count {key!r} is missing or invalid")
    for key in _REQUIRED_NPM_SEVERITY_COUNTS:
        value = severity_counts.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InputError(f"npm vulnerability count {key!r} is missing or invalid")
    reported_vulnerability_total = severity_counts["total"]
    if (
        sum(severity_counts[key] for key in _REQUIRED_NPM_SEVERITY_COUNTS[:-1])
        != reported_vulnerability_total
    ):
        raise InputError("npm vulnerability severity counts do not add up to total")

    vulnerabilities = _object(report.get("vulnerabilities"), "npm vulnerabilities")
    if len(vulnerabilities) != reported_vulnerability_total:
        raise InputError(
            "npm vulnerability count/total does not match the reported vulnerability entries"
        )
    reachable = _npm_reachable_nodes(packages)
    findings: list[dict[str, Any]] = []
    structured_names: set[str] = set()
    references: dict[str, set[str]] = {}
    row_severity_counts = {severity: 0 for severity in _REQUIRED_NPM_SEVERITY_COUNTS[:-1]}
    for reported_name, raw_vulnerability in vulnerabilities.items():
        vulnerability = _object(raw_vulnerability, f"npm vulnerability {reported_name}")
        package_name = _string(vulnerability.get("name"), f"npm vulnerability {reported_name} name")
        if package_name != reported_name:
            raise InputError(f"npm vulnerability key/name mismatch for {reported_name!r}")
        severity = _string(
            vulnerability.get("severity"), f"npm vulnerability {reported_name} severity"
        ).lower()
        if severity not in row_severity_counts:
            raise InputError(f"npm vulnerability {reported_name!r} has an invalid severity")
        row_severity_counts[severity] += 1
        via = vulnerability.get("via")
        nodes = vulnerability.get("nodes")
        if not isinstance(via, list) or not isinstance(nodes, list) or not nodes:
            raise InputError(
                f"npm vulnerability {reported_name!r} lacks via details or lockfile nodes"
            )

        resolved_nodes: list[tuple[str, str, str]] = []
        for node in nodes:
            node_path = _string(node, f"npm vulnerability {reported_name} node")
            package = packages.get(node_path)
            if not isinstance(package, Mapping):
                raise InputError(
                    f"npm audit node {node_path!r} is not present in package-lock.json"
                )
            if node_path not in reachable:
                raise InputError(
                    f"npm audit node {node_path!r} is not reachable in "
                    "the lockfile dependency graph"
                )
            normalized_node = node_path.replace("\\", "/")
            marker = "node_modules/"
            if marker not in normalized_node:
                raise InputError(f"npm audit node has invalid package path: {node_path!r}")
            leaf_name = normalized_node.rsplit(marker, 1)[1]
            if leaf_name != package_name:
                raise InputError(
                    f"npm audit node {node_path!r} does not resolve to {package_name!r}"
                )
            locked_version = _version(package.get("version"), f"npm lock version for {node_path}")
            # npm marks packages reachable exclusively from development roots with dev=true.
            # Missing/false flags are conservatively treated as runtime exposure.
            scope = "development" if package.get("dev") is True else "runtime"
            resolved_nodes.append((node_path, locked_version, scope))

        string_references = {item for item in via if isinstance(item, str)}
        if string_references:
            references[reported_name] = string_references
        if "fixAvailable" not in vulnerability:
            raise InputError(f"npm vulnerability {reported_name!r} is missing fixAvailable data")
        fix_available = vulnerability["fixAvailable"]
        if not isinstance(fix_available, (bool, Mapping)):
            raise InputError(f"npm vulnerability {reported_name!r} has invalid fixAvailable data")
        fix_available_matches_leaf = fix_available is True
        if isinstance(fix_available, Mapping):
            fix_package = _string(
                fix_available.get("name"),
                f"npm vulnerability {reported_name} fixAvailable name",
            )
            _version(
                fix_available.get("version"),
                f"npm vulnerability {reported_name} fixAvailable version",
            )
            if not isinstance(fix_available.get("isSemVerMajor"), bool):
                raise InputError(
                    f"npm vulnerability {reported_name!r} has invalid fixAvailable data"
                )
            fix_available_matches_leaf = _canonical_name(fix_package) == _canonical_name(
                package_name
            )
        for advisory in via:
            if not isinstance(advisory, Mapping):
                continue
            advisory_name = _string(advisory.get("name"), f"npm advisory on {reported_name} name")
            if advisory_name != package_name:
                # A propagated parent row may carry metadata for its underlying dependency.
                continue
            structured_names.add(package_name)
            advisory_ids: set[str] = set()
            for field in ("source", "url", "title", "id", "aliases"):
                advisory_ids.update(_identifiers(advisory.get(field)))
            if not advisory_ids:
                raise InputError(
                    f"npm advisory for {package_name!r} lacks a GHSA, PYSEC, or CVE identifier"
                )
            explicit_fixes = advisory.get("fix_versions", advisory.get("fixed_versions", []))
            if explicit_fixes is None:
                explicit_fixes = []
            if not isinstance(explicit_fixes, list) or any(
                not isinstance(item, str) for item in explicit_fixes
            ):
                raise InputError(
                    f"npm advisory for {package_name!r} has invalid fixed-version data"
                )
            known_fixed = bool(explicit_fixes) or fix_available_matches_leaf
            for node_path, locked_version, scope in resolved_nodes:
                _append_deduplicated(
                    findings,
                    {
                        "ecosystem": "npm",
                        "package": package_name,
                        "version": locked_version,
                        "scope": scope,
                        "advisory_ids": sorted(advisory_ids),
                        "nodes": [node_path],
                        "known_fixed_release": known_fixed,
                        "source": "npm-audit",
                    },
                )
    for severity, reported_count in row_severity_counts.items():
        if reported_count != severity_counts[severity]:
            raise InputError(
                f"npm vulnerability {severity} severity count does not match report entries"
            )
    if vulnerabilities and not structured_names:
        raise InputError("npm report has no structured leaf advisory details")

    def reaches_structured_leaf(package_name: str, seen: set[str]) -> bool:
        if package_name in structured_names:
            return True
        if package_name in seen:
            return False
        seen.add(package_name)
        return any(
            reaches_structured_leaf(target, seen) for target in references.get(package_name, set())
        )

    for package_name, targets in references.items():
        for target in targets:
            if target not in vulnerabilities:
                raise InputError(
                    f"npm vulnerability {package_name!r} references missing entry {target!r}"
                )
        if not reaches_structured_leaf(package_name, set()):
            raise InputError(
                f"npm vulnerability {package_name!r} does not resolve to structured leaf details"
            )
    return findings


def _insert_locked_python_package(
    packages: dict[str, str], name: str, version: str, source: Path
) -> None:
    key = _canonical_name(name)
    previous = packages.get(key)
    if previous is not None and previous != version:
        raise InputError(f"conflicting versions for Python package {name!r} in {source}")
    packages[key] = version


def _read_requirements(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    visited = set() if seen is None else seen
    resolved_path = path.resolve()
    if resolved_path in visited:
        raise InputError(f"recursive requirements include detected at {path}")
    visited.add(resolved_path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise InputError(f"Python dependency lock could not be read: {path}: {error}") from error
    packages: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        include = re.fullmatch(r"-r\s+(.+)", line)
        if include:
            included_path = (path.parent / include.group(1).strip()).resolve()
            for name, version in _read_requirements(included_path, visited).items():
                _insert_locked_python_package(packages, name, version, path)
            continue
        match = _REQUIREMENT_RE.fullmatch(line)
        if not match:
            raise InputError(f"unsupported or unpinned Python requirement at {path}:{line_number}")
        _insert_locked_python_package(packages, match.group(1), match.group(2), path)
    visited.remove(resolved_path)
    return packages


def _python_report(
    report: Mapping[str, Any], runtime_requirements: Path, dev_requirements: Path
) -> list[dict[str, Any]]:
    runtime_lock = _read_requirements(runtime_requirements)
    dev_lock = _read_requirements(dev_requirements)
    if not set(runtime_lock).issubset(dev_lock):
        raise InputError("requirements-dev.txt must include every package from requirements.txt")

    raw_dependencies = report.get("dependencies")
    if not isinstance(raw_dependencies, list):
        raise InputError("pip-audit report is incomplete: dependencies must be an array")
    inventory: dict[str, tuple[str, list[Any]]] = {}
    for index, raw_dependency in enumerate(raw_dependencies):
        dependency = _object(raw_dependency, f"pip-audit dependency {index}")
        name = _string(dependency.get("name"), f"pip-audit dependency {index} name")
        normalized_name = _canonical_name(name)
        version = _version(dependency.get("version"), f"pip-audit version for {name}")
        vulnerabilities = dependency.get("vulns")
        if not isinstance(vulnerabilities, list):
            raise InputError(f"pip-audit dependency {name!r} lacks a vulnerability list")
        previous = inventory.get(normalized_name)
        if previous and (previous[0] != version or previous[1] != vulnerabilities):
            raise InputError(f"pip-audit report contains conflicting entries for {name!r}")
        inventory[normalized_name] = (version, vulnerabilities)

    if set(inventory) != set(dev_lock):
        missing = sorted(set(dev_lock) - set(inventory))
        unexpected = sorted(set(inventory) - set(dev_lock))
        details = []
        if missing:
            details.append(f"missing locked packages: {', '.join(missing)}")
        if unexpected:
            details.append(f"packages absent from requirements locks: {', '.join(unexpected)}")
        raise InputError(
            "pip-audit inventory does not match the full pinned requirements: " + "; ".join(details)
        )

    findings: list[dict[str, Any]] = []
    for package_name, (reported_version, vulnerabilities) in inventory.items():
        if reported_version != dev_lock[package_name]:
            raise InputError(
                f"pip-audit version for {package_name!r} does not match the pinned requirements"
            )
        scope = "runtime" if package_name in runtime_lock else "development"
        for index, raw_vulnerability in enumerate(vulnerabilities):
            vulnerability = _object(
                raw_vulnerability, f"pip-audit vulnerability for {package_name}"
            )
            primary = _string(vulnerability.get("id"), f"pip-audit advisory id for {package_name}")
            aliases = vulnerability.get("aliases", [])
            if not isinstance(aliases, list) or any(
                not isinstance(alias, str) for alias in aliases
            ):
                raise InputError(
                    f"pip-audit aliases for {package_name!r} must be an array of strings"
                )
            advisory_ids = {primary.upper(), *[alias.upper() for alias in aliases]}
            if any(not _IDENTIFIER_RE.fullmatch(identifier) for identifier in advisory_ids):
                raise InputError(
                    f"pip-audit advisory for {package_name!r} has an unsupported identifier"
                )
            fixed_versions = vulnerability.get("fix_versions")
            if not isinstance(fixed_versions, list) or any(
                not isinstance(item, str) for item in fixed_versions
            ):
                raise InputError(
                    f"pip-audit fixed versions for {package_name!r} are missing or malformed"
                )
            _append_deduplicated(
                findings,
                {
                    "ecosystem": "pypi",
                    "package": package_name,
                    "version": reported_version,
                    "scope": scope,
                    "advisory_ids": sorted(advisory_ids),
                    "known_fixed_release": bool(fixed_versions),
                    "source": "pip-audit",
                    "report_index": index,
                },
            )
    return findings


def _append_deduplicated(findings: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    candidate_ids = set(candidate["advisory_ids"])
    for existing in findings:
        same_artifact = all(
            existing[field] == candidate[field]
            for field in ("ecosystem", "package", "version", "scope")
        )
        if same_artifact and candidate_ids.intersection(existing["advisory_ids"]):
            existing["advisory_ids"] = sorted(set(existing["advisory_ids"]) | candidate_ids)
            existing["known_fixed_release"] = (
                existing["known_fixed_release"] or candidate["known_fixed_release"]
            )
            if "nodes" in candidate:
                existing["nodes"] = sorted(set(existing.get("nodes", [])) | set(candidate["nodes"]))
            return
    findings.append(candidate)


def _validated_exceptions(document: Mapping[str, Any], now: datetime) -> list[dict[str, str]]:
    if document.get("schema_version") != 1 or not isinstance(document.get("exceptions"), list):
        raise InputError("exception policy must contain schema_version 1 and an exceptions array")
    exceptions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for index, raw_exception in enumerate(document["exceptions"]):
        exception = _object(raw_exception, f"exception {index}")
        if set(exception) != _EXCEPTION_FIELDS:
            raise InputError(f"exception {index} must use only the exact required fields")
        ecosystem = _string(exception.get("ecosystem"), f"exception {index} ecosystem")
        if ecosystem not in ("npm", "pypi"):
            raise InputError(f"exception {index} has unsupported ecosystem")
        advisory = _string(exception.get("advisory"), f"exception {index} advisory").upper()
        if not _IDENTIFIER_RE.fullmatch(advisory):
            raise InputError(f"exception {index} advisory must be one exact GHSA, PYSEC, or CVE id")
        package = _string(exception.get("package"), f"exception {index} package")
        version = _version(exception.get("version"), f"exception {index} version")
        scope = _string(exception.get("scope"), f"exception {index} scope")
        if scope not in ("runtime", "development"):
            raise InputError(f"exception {index} scope must be runtime or development")
        owner = _string(exception.get("owner"), f"exception {index} owner")
        evidence = _string(exception.get("evidence"), f"exception {index} evidence")
        if len(evidence) < 24:
            raise InputError(
                f"exception {index} evidence must explain the concrete dependency "
                "and mitigation state"
            )
        expiration_text = _string(exception.get("expires_at"), f"exception {index} expires_at")
        try:
            expires_at = datetime.fromisoformat(expiration_text)
        except ValueError as error:
            raise InputError(
                f"exception {index} expires_at must be an ISO-8601 timestamp"
            ) from error
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise InputError(f"exception {index} expires_at must include a timezone")
        if expires_at.astimezone(UTC) <= now.astimezone(UTC):
            raise InputError(f"exception {index} for {advisory} has expired")
        # The policy caps calendar dates in the exception timestamp's timezone.
        # This permits the plan's end-of-day timestamp on its 30th date.
        if expires_at.date() - now.astimezone(expires_at.tzinfo).date() > timedelta(days=30):
            raise InputError(f"exception {index} for {advisory} exceeds the 30-day maximum")
        normalized = {
            "ecosystem": ecosystem,
            "advisory": advisory,
            "package": package,
            "version": version,
            "scope": scope,
            "owner": owner,
            "expires_at": expiration_text,
            "evidence": evidence,
        }
        identity = (ecosystem, advisory, _canonical_name(package), version, scope)
        if identity in seen:
            raise InputError(f"exception {index} duplicates an existing exact exception")
        seen.add(identity)
        exceptions.append(normalized)
    return exceptions


def evaluate_reports(
    *,
    npm_report: Mapping[str, Any],
    npm_lock: Mapping[str, Any],
    python_report: Mapping[str, Any],
    runtime_requirements: Path,
    dev_requirements: Path,
    exceptions: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate, deduplicate, and disposition all findings from complete collector reports."""
    evaluation_time = now or datetime.now(UTC)
    decision: dict[str, Any] = {
        "schema_version": 1,
        "status": "approved",
        "generated_at": evaluation_time.astimezone(UTC).isoformat(),
        "findings": [],
        "exceptions": [],
        "errors": [],
        "exit_code": 0,
    }
    try:
        packages = _npm_lock_packages(npm_lock)
        findings = _npm_report(npm_report, packages)
        findings.extend(_python_report(python_report, runtime_requirements, dev_requirements))
        validated_exceptions = _validated_exceptions(exceptions, evaluation_time)
        consumed: set[int] = set()
        for finding in findings:
            matching = [
                (index, exception)
                for index, exception in enumerate(validated_exceptions)
                if exception["ecosystem"] == finding["ecosystem"]
                and exception["advisory"] in finding["advisory_ids"]
                and _canonical_name(exception["package"]) == _canonical_name(finding["package"])
                and exception["version"] == finding["version"]
                and exception["scope"] == finding["scope"]
            ]
            if len(matching) > 1:
                raise InputError(
                    f"multiple exceptions match {finding['package']} {finding['version']}"
                )
            if matching:
                index, exception = matching[0]
                if finding["known_fixed_release"]:
                    raise InputError(
                        f"exception for {finding['package']} {finding['version']} is invalid: "
                        "a fixed release is available"
                    )
                consumed.add(index)
                finding["disposition"] = "exception_approved"
                finding["exception"] = {
                    "owner": exception["owner"],
                    "expires_at": exception["expires_at"],
                }
            else:
                finding["disposition"] = "unhandled"
        decision["findings"] = sorted(
            findings,
            key=lambda finding: (
                finding["ecosystem"],
                finding["package"],
                finding["version"],
                finding["scope"],
                finding["advisory_ids"],
            ),
        )
        if any(finding["disposition"] == "unhandled" for finding in findings):
            decision["status"] = "blocked"
            decision["exit_code"] = 1
        decision["exceptions"] = [
            {
                "ecosystem": exception["ecosystem"],
                "advisory": exception["advisory"],
                "package": exception["package"],
                "version": exception["version"],
                "scope": exception["scope"],
                "owner": exception["owner"],
                "expires_at": exception["expires_at"],
                "disposition": "applied" if index in consumed else "unmatched",
            }
            for index, exception in enumerate(validated_exceptions)
        ]
    except InputError as error:
        decision["status"] = "error"
        decision["errors"] = [str(error)]
        decision["exit_code"] = 2
    return decision


def _write_json(path: Path, document: Mapping[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True
    except OSError as error:
        print(f"could not write decision report {path}: {error}", file=sys.stderr)
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm-report", type=Path, required=True)
    parser.add_argument("--npm-lock", type=Path, required=True)
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        decision = evaluate_reports(
            npm_report=_json_file(arguments.npm_report, "npm audit report"),
            npm_lock=_json_file(arguments.npm_lock, "npm lock"),
            python_report=_json_file(arguments.python_report, "pip-audit report"),
            runtime_requirements=Path("requirements.txt"),
            dev_requirements=Path("requirements-dev.txt"),
            exceptions=_json_file(arguments.exceptions, "exception policy"),
        )
    except InputError as error:
        decision = {
            "schema_version": 1,
            "status": "error",
            "generated_at": datetime.now(UTC).isoformat(),
            "findings": [],
            "exceptions": [],
            "errors": [str(error)],
            "exit_code": 2,
        }
    if not _write_json(arguments.output, decision):
        return 2
    print(f"dependency advisory gate: {decision['status']} ({len(decision['findings'])} findings)")
    if decision["errors"]:
        for error in decision["errors"]:
            print(f"error: {error}", file=sys.stderr)
    for finding in decision["findings"]:
        print(
            f"{finding['disposition']}: {finding['ecosystem']} {finding['package']} "
            f"{finding['version']} [{finding['scope']}] ({', '.join(finding['advisory_ids'])})"
        )
    return int(decision["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
