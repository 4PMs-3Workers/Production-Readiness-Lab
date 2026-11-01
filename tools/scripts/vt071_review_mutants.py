"""Run deliberate bugs on an isolated Git branch; always restore submission source.

Use a clean submission checkout and a dedicated MySQL 8.4 test server. One mutant
drops an index only in the UUID-named schema created by the test fixture; the final
test source and production schema never contain that DDL.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/assignments/VT-071/evidence"
SOURCE = "src/backend/app/services/booking.py"
REPO = "src/backend/app/repositories/appointments.py"
API = "src/backend/app/api/appointments.py"
EXPORT = "src/backend/app/services/appointment_export.py"
TEST = "tests/backend/integration/test_appointment_export.py"
FILES = [SOURCE, REPO, API, EXPORT, TEST]
BRANCH = "codex/bt3-23001952-vt071-mutants-review"


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()


def replace_in_function(name, function, old, new):
    path = ROOT / name
    original = path.read_text(encoding="utf-8")
    start = original.index("async def " + function + "(")
    next_function = original.find("\n\nasync def ", start + 1)
    next_sync = original.find("\n\ndef ", start + 1)
    ends = [value for value in [next_function, next_sync] if value != -1]
    end = min(ends) if ends else len(original)
    segment = original[start:end]
    assert segment.count(old) == 1, (name, function, old)
    path.write_text(original[:start] + segment.replace(old, new) + original[end:], encoding="utf-8")


def mutate(kind):
    if kind == "owner":
        replace_in_function(
            REPO,
            "read_student_appointment_export",
            "_student_detail_statement().where(Appointment.student_id == student_id)",
            "_student_detail_statement()",
        )
    elif kind in {"role_primary", "role_all"}:
        path = ROOT / API
        value = path.read_text(encoding="utf-8")
        value = value.replace(
            "from app.dependencies.auth import require_csrf",
            "from app.dependencies.auth import require_auth, require_csrf",
        )
        path.write_text(value, encoding="utf-8")
        replace_in_function(
            API, "export_appointments", "Depends(require_student)", "Depends(require_auth)"
        )
        if kind == "role_all":
            replace_in_function(
                API,
                "export_appointments",
                "    _query_guard: Annotated[None, Depends(reject_duplicate_catalog_query)],\n",
                "",
            )
            replace_in_function(API, "export_appointments", "    del _query_guard\n", "")
    elif kind == "overflow":
        replace_in_function(
            EXPORT,
            "export_student_appointments",
            'raise BusinessConflict("EXPORT_TOO_LARGE", '
            '"Export exceeds 1000 rows; filter by status")',
            "pass  # Deliberate mutation: allow oversized CSV",
        )
    elif kind == "source_and_unique":
        path = ROOT / SOURCE
        value = path.read_text(encoding="utf-8")
        assert value.count("    return _receipt(row), True") == 1
        path.write_text(
            value.replace("    return _receipt(row), True", "    return None"), encoding="utf-8"
        )
        replace_in_function(
            TEST,
            "test_export_source_cardinality_after_retry",
            "    await sign_in(env)\n",
            "    from re import fullmatch\n"
            "    from sqlalchemy import text\n"
            '    schema = env.factory.kw["bind"].url.database\n'
            '    assert fullmatch(r"prl_models_test_[a-f0-9]{32}", schema)\n'
            "    async with env.factory.begin() as db:\n"
            '        await db.execute(text("ALTER TABLE appointments DROP INDEX '
            'uq_appointments_creation_request"))\n'
            "    await sign_in(env)\n",
        )
    else:
        raise ValueError(kind)


def main():
    if not os.getenv("MYSQL_TEST_URL"):
        raise RuntimeError("Dedicated MYSQL_TEST_URL required")
    if git("status", "--porcelain"):
        raise RuntimeError("Commit current work before creating experimental branch")
    submission = git("branch", "--show-current")
    baseline = git("rev-parse", "HEAD")
    git("switch", "-c", BRANCH)
    cases = [
        ("owner", "test_export_real_login_scope_contract_and_inactive_catalog", "KILLED"),
        ("overflow", "test_export_rejects_overflow_instead_of_truncation", "KILLED"),
        ("role_primary", "test_export_real_session_role_is_denied", "SURVIVED"),
        ("role_all", "test_export_real_session_role_is_denied", "KILLED"),
        ("source_and_unique", "test_export_source_cardinality_after_retry", "KILLED"),
    ]
    results = []
    try:
        for kind, test, expected in cases:
            git("restore", "--source=" + baseline, "--", *FILES)
            mutate(kind)
            git("add", "--", *FILES)
            git("commit", "-m", f"experiment(vt071): deliberate {kind} mutation (do not merge)")
            commit = git("rev-parse", "HEAD")
            patch = git("diff", baseline, "--", *FILES)
            (EVIDENCE / f"review-mutant-{kind}.patch.txt").write_text(
                patch + "\n", encoding="utf-8"
            )
            command = [
                sys.executable,
                "-m",
                "pytest",
                f"{TEST}::{test}",
                "-q",
                "--require-mysql",
                "--tb=short",
            ]
            run = subprocess.run(
                command,
                cwd=ROOT,
                env=dict(os.environ, PYTHONUTF8="1"),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            log = run.stdout + run.stderr + f"\nEXIT_CODE={run.returncode}\n"
            (EVIDENCE / f"review-mutant-{kind}.txt").write_text(log, encoding="utf-8")
            outcome = (
                "SURVIVED"
                if run.returncode == 0
                else (
                    "KILLED"
                    if run.returncode == 1 and "AssertionError" in log
                    else "EXECUTION_ERROR"
                )
            )
            result = {
                "mutant": kind,
                "commit": commit,
                "test": test,
                "expected": expected,
                "observed": outcome,
                "exit_code": run.returncode,
            }
            results.append(result)
            print(json.dumps(result), flush=True)
    finally:
        git("restore", "--source=" + baseline, "--", *FILES)
        git("add", "--", *FILES)
        if git("diff", "--cached", "--name-only"):
            git(
                "commit",
                "-m",
                "experiment(vt071): restore all production/test source after mutations",
            )
        restored_commit = git("rev-parse", "HEAD")
        git("switch", submission)
        (EVIDENCE / "review-mutants.json").write_text(
            json.dumps(
                {
                    "submission_source_commit": baseline,
                    "experimental_branch": BRANCH,
                    "experimental_restored_head": restored_commit,
                    "mutants": results,
                    "submission_restore_diff": git("diff", baseline, "--", *FILES),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    assert all(r["expected"] == r["observed"] for r in results), results


if __name__ == "__main__":
    main()
