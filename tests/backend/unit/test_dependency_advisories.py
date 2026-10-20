from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tools.scripts.check_dependency_advisories import evaluate_reports, main

NOW = datetime(2026, 10, 19, 16, 59, 59, tzinfo=UTC)
BRACES_ADVISORY = "GHSA-vfj7-8cjw-p6xm"


def npm_report(*, fix_available: bool = False, duplicate_alias: bool = False) -> dict[str, Any]:
    advisory: dict[str, Any] = {
        "source": 123456,
        "name": "braces",
        "dependency": "braces",
        "title": "CVE-2099-12345: Regular expression denial of service",
        "url": f"https://github.com/advisories/{BRACES_ADVISORY}",
        "severity": "moderate",
        "range": "<3.0.4",
    }
    via = [advisory]
    if duplicate_alias:
        via.append({**advisory, "source": 654321, "title": f"Duplicate {BRACES_ADVISORY}"})
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "braces": {
                "name": "braces",
                "severity": "moderate",
                "isDirect": False,
                "via": via,
                "effects": [],
                "range": "<3.0.4",
                "nodes": ["node_modules/braces"],
                "fixAvailable": fix_available,
            }
        },
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 1,
                "high": 0,
                "critical": 0,
                "total": 1,
            },
            "dependencies": {
                "prod": 0,
                "dev": 3,
                "optional": 0,
                "peer": 0,
                "peerOptional": 0,
                "total": 3,
            },
        },
    }


def npm_lock(*, version: str = "3.0.3", dev: bool = True) -> dict[str, Any]:
    return {
        "name": "fixture",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"name": "fixture", "devDependencies": {"eslint": "1.0.0"}},
            "node_modules/eslint": {
                "version": "1.0.0",
                "dev": True,
                "dependencies": {"micromatch": "4.0.8"},
            },
            "node_modules/micromatch": {
                "version": "4.0.8",
                "dev": True,
                "dependencies": {"braces": "^3.0.3"},
            },
            "node_modules/braces": {"version": version, "dev": dev},
        },
    }


def python_report(*, duplicate_alias: bool = False, omit: str | None = None) -> dict[str, Any]:
    clean = [
        {"name": "requests", "version": "2.31.0", "vulns": []},
        {"name": "pytest", "version": "9.0.3", "vulns": []},
    ]
    if duplicate_alias:
        ids = [
            {
                "id": "PYSEC-2099-1",
                "aliases": ["GHSA-aaaa-bbbb-cccc", "CVE-2099-0001"],
                "fix_versions": [],
                "description": "Example advisory",
            },
            {
                "id": "GHSA-aaaa-bbbb-cccc",
                "aliases": ["PYSEC-2099-1", "CVE-2099-0001"],
                "fix_versions": [],
                "description": "Same advisory through another alias",
            },
        ]
        clean[0]["vulns"] = ids
    if omit:
        clean = [item for item in clean if item["name"] != omit]
    return {"dependencies": clean}


def exception(
    *, version: str = "3.0.3", expires: str = "2026-11-18T23:59:59+07:00"
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "exceptions": [
            {
                "ecosystem": "npm",
                "advisory": BRACES_ADVISORY,
                "package": "braces",
                "version": version,
                "scope": "development",
                "owner": "repository-maintainers",
                "expires_at": expires,
                "evidence": (
                    "The locked lint dependency chain requires braces 3.0.3; "
                    "the advisory has no fixed braces release."
                ),
            }
        ],
    }


def requirements(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "requirements.txt"
    development = tmp_path / "requirements-dev.txt"
    runtime.write_text("requests==2.31.0\n", encoding="utf-8")
    development.write_text("-r requirements.txt\npytest==9.0.3\n", encoding="utf-8")
    return runtime, development


def evaluate(
    tmp_path: Path,
    *,
    npm: dict[str, Any] | None = None,
    lock: dict[str, Any] | None = None,
    python: dict[str, Any] | None = None,
    exceptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime, development = requirements(tmp_path)
    return evaluate_reports(
        npm_report=npm if npm is not None else npm_report(),
        npm_lock=lock if lock is not None else npm_lock(),
        python_report=python if python is not None else python_report(),
        runtime_requirements=runtime,
        dev_requirements=development,
        exceptions=exceptions if exceptions is not None else exception(),
        now=NOW,
    )


def test_safe_exact_braces_exception_is_approved(tmp_path: Path) -> None:
    decision = evaluate(tmp_path)

    assert decision["status"] == "approved"
    assert len(decision["findings"]) == 1
    assert decision["findings"][0]["package"] == "braces"
    assert decision["findings"][0]["disposition"] == "exception_approved"
    assert decision["findings"][0]["scope"] == "development"


def test_exception_version_must_match_exact_locked_version(tmp_path: Path) -> None:
    decision = evaluate(tmp_path, exceptions=exception(version="3.0.2"))

    assert decision["status"] == "blocked"
    assert decision["exit_code"] == 1
    assert decision["findings"][0]["disposition"] == "unhandled"


def test_ghsa_cve_and_pysec_aliases_deduplicate_to_one_finding(tmp_path: Path) -> None:
    decision = evaluate(
        tmp_path,
        npm=npm_report(duplicate_alias=True),
        python=python_report(duplicate_alias=True),
    )

    python_findings = [
        finding for finding in decision["findings"] if finding["ecosystem"] == "pypi"
    ]
    assert len(python_findings) == 1
    assert set(python_findings[0]["advisory_ids"]) == {
        "PYSEC-2099-1",
        "GHSA-AAAA-BBBB-CCCC",
        "CVE-2099-0001",
    }
    assert decision["status"] == "blocked"


def test_expired_exception_is_a_policy_error(tmp_path: Path) -> None:
    decision = evaluate(tmp_path, exceptions=exception(expires="2026-10-18T23:59:59+07:00"))

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("expired" in error.lower() for error in decision["errors"])


def test_exception_is_rejected_when_a_fixed_release_is_known(tmp_path: Path) -> None:
    decision = evaluate(tmp_path, npm=npm_report(fix_available=True))

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("fixed" in error.lower() for error in decision["errors"])


def test_parent_package_fix_does_not_invalidate_leaf_exception(tmp_path: Path) -> None:
    report = npm_report()
    report["vulnerabilities"]["braces"]["fixAvailable"] = {
        "name": "@next/eslint-plugin-next",
        "version": "14.2.35",
        "isSemVerMajor": True,
    }

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "approved"
    assert decision["exit_code"] == 0
    assert decision["findings"][0]["known_fixed_release"] is False
    assert decision["findings"][0]["disposition"] == "exception_approved"


def test_leaf_package_fix_still_invalidates_exception(tmp_path: Path) -> None:
    report = npm_report()
    report["vulnerabilities"]["braces"]["fixAvailable"] = {
        "name": "braces",
        "version": "3.0.4",
        "isSemVerMajor": False,
    }

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("fixed" in error.lower() for error in decision["errors"])


def test_npm_v2_missing_fix_available_fails_closed_for_exact_exception(tmp_path: Path) -> None:
    report = npm_report()
    del report["vulnerabilities"]["braces"]["fixAvailable"]

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("fixavailable" in error.lower() for error in decision["errors"])


def test_npm_v2_invalid_fix_available_shape_fails_closed(tmp_path: Path) -> None:
    report = npm_report()
    report["vulnerabilities"]["braces"]["fixAvailable"] = "false"

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("fixavailable" in error.lower() for error in decision["errors"])


def test_npm_v2_incomplete_fix_available_object_fails_closed(tmp_path: Path) -> None:
    report = npm_report()
    report["vulnerabilities"]["braces"]["fixAvailable"] = {
        "name": "braces",
        "version": "3.0.4",
    }

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("fixavailable" in error.lower() for error in decision["errors"])


def test_npm_v2_vulnerability_total_must_match_package_rows(tmp_path: Path) -> None:
    report = npm_report()
    report["metadata"]["vulnerabilities"]["moderate"] = 2
    report["metadata"]["vulnerabilities"]["total"] = 2

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any(
        "count" in error.lower() or "metadata" in error.lower() for error in decision["errors"]
    )


def test_npm_v2_severity_counts_must_match_package_rows(tmp_path: Path) -> None:
    report = npm_report()
    report["vulnerabilities"]["braces"]["severity"] = "high"

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("severity" in error.lower() for error in decision["errors"])


def test_npm_report_node_must_be_reachable_from_a_locked_dependency_root(tmp_path: Path) -> None:
    lock = npm_lock()
    lock["packages"]["node_modules/micromatch"]["dependencies"] = {}

    decision = evaluate(tmp_path, lock=lock)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("not reachable" in error.lower() for error in decision["errors"])


def test_npm_parent_alias_must_resolve_to_structured_leaf_advisory(tmp_path: Path) -> None:
    report = npm_report()
    report["vulnerabilities"]["braces"]["via"] = ["missing-leaf"]

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("no structured leaf" in error.lower() for error in decision["errors"])


@pytest.mark.parametrize(
    "via",
    [
        [],
        [None],
        [123],
        [False],
        [""],
        [{"name": "braces", "url": f"https://github.com/advisories/{BRACES_ADVISORY}"}],
        ["eslint"],
    ],
    ids=["empty", "null", "number", "boolean", "blank-reference", "unrelated-leaf", "cycle"],
)
def test_every_npm_vulnerability_needs_resolvable_advisory_details(
    tmp_path: Path, via: list[Any]
) -> None:
    report = npm_report()
    report["vulnerabilities"]["eslint"] = {
        "name": "eslint",
        "severity": "high",
        "isDirect": True,
        "via": via,
        "effects": [],
        "range": "*",
        "nodes": ["node_modules/eslint"],
        "fixAvailable": False,
    }
    report["metadata"]["vulnerabilities"].update(high=1, total=2)

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("eslint" in error.lower() for error in decision["errors"])


def test_valid_npm_parent_chain_keeps_the_exact_leaf_exception(tmp_path: Path) -> None:
    report = npm_report()
    for name, target in (("eslint", "micromatch"), ("micromatch", "braces")):
        report["vulnerabilities"][name] = {
            "name": name,
            "severity": "moderate",
            "isDirect": name == "eslint",
            "via": [target],
            "effects": [],
            "range": "*",
            "nodes": [f"node_modules/{name}"],
            "fixAvailable": False,
        }
    report["metadata"]["vulnerabilities"].update(moderate=3, total=3)

    decision = evaluate(tmp_path, npm=report)

    assert decision["status"] == "approved"
    assert decision["exit_code"] == 0
    assert len(decision["findings"]) == 1
    assert decision["findings"][0]["package"] == "braces"
    assert decision["findings"][0]["disposition"] == "exception_approved"


def test_exception_scope_must_match_runtime_or_development_exposure(tmp_path: Path) -> None:
    decision = evaluate(tmp_path, lock=npm_lock(dev=False))

    assert decision["status"] == "blocked"
    assert decision["exit_code"] == 1
    assert decision["findings"][0]["scope"] == "runtime"


def test_exception_expiry_cannot_exceed_30_days(tmp_path: Path) -> None:
    decision = evaluate(tmp_path, exceptions=exception(expires="2026-11-19T23:59:59+07:00"))

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert any("30-day maximum" in error for error in decision["errors"])


@pytest.mark.parametrize(
    ("npm", "python"),
    [
        ({"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {}}, None),
        (None, {"dependencies": [{"name": "requests", "version": "2.31.0", "vulns": []}]}),
    ],
)
def test_malformed_or_incomplete_reports_fail_closed(
    tmp_path: Path,
    npm: dict[str, Any] | None,
    python: dict[str, Any] | None,
) -> None:
    decision = evaluate(tmp_path, npm=npm, python=python)

    assert decision["status"] == "error"
    assert decision["exit_code"] == 2
    assert decision["errors"]


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_exit"),
    [
        ("complete", "approved", 0),
        ("missing", "error", 2),
        ("wrong-version", "error", 2),
        ("unexpected-tool-package", "error", 2),
        ("advisory", "blocked", 1),
    ],
)
def test_bootstrap_dependency_requires_a_complete_audited_inventory(
    tmp_path: Path, state: str, expected_status: str, expected_exit: int
) -> None:
    runtime, development = requirements(tmp_path)
    development.write_text(
        development.read_text(encoding="utf-8") + "packaging==26.3\n", encoding="utf-8"
    )
    report = python_report()
    package: dict[str, Any] = {"name": "packaging", "version": "26.3", "vulns": []}
    if state == "wrong-version":
        package["version"] = "26.2"
    elif state == "advisory":
        package["vulns"] = [{"id": "PYSEC-2099-2", "aliases": [], "fix_versions": []}]
    if state != "missing":
        report["dependencies"].append(package)
    if state == "unexpected-tool-package":
        report["dependencies"].append({"name": "wheel", "version": "0.46.3", "vulns": []})
    decision = evaluate_reports(
        npm_report=npm_report(),
        npm_lock=npm_lock(),
        python_report=report,
        runtime_requirements=runtime,
        dev_requirements=development,
        exceptions=exception(),
        now=NOW,
    )
    assert decision["status"] == expected_status
    assert decision["exit_code"] == expected_exit
    if state == "missing":
        assert any("missing locked packages: packaging" in error for error in decision["errors"])
    elif state == "advisory":
        finding = next(f for f in decision["findings"] if f["package"] == "packaging")
        assert finding["scope"] == "development"
        assert finding["disposition"] == "unhandled"


def test_cli_writes_the_decision_and_returns_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    requirements(tmp_path)
    files = {
        "npm.json": npm_report(),
        "lock.json": npm_lock(),
        "python.json": python_report(),
        "exceptions.json": exception(expires=(datetime.now(UTC) + timedelta(days=30)).isoformat()),
    }
    for filename, document in files.items():
        (tmp_path / filename).write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "build" / "security" / "decision.json"

    result = main(
        [
            "--npm-report",
            "npm.json",
            "--npm-lock",
            "lock.json",
            "--python-report",
            "python.json",
            "--exceptions",
            "exceptions.json",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "approved"
