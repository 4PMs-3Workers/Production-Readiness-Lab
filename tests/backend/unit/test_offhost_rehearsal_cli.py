import json
import sys

import pytest
from tools.backup.offhost_contract import Code, RemoteError
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_rehearsal import cli_report


def child(report, exit_code):
    return [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('private-diagnostic-must-not-leak'); "
        f"print({json.dumps(json.dumps(report))}); sys.exit({exit_code})",
    ]


@pytest.mark.parametrize("reason", [Code.TRANSPORT, Code.IDENTITY, Code.KEY, Code.INTEGRITY])
def test_rehearsal_preserves_validated_failure_reason_without_raw_output(reason, capfd):
    report = dict(operation="upload", success=False, reason=reason, duration_seconds=0.0)
    with pytest.raises(RemoteError) as failure:
        cli_report(child(report, 1), "upload", Deadline(10))
    assert failure.value.code == reason
    assert str(failure.value) == reason.value
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize(
    "changes,exit_code",
    [
        ({"operation": "status"}, 0),
        ({"success": False, "reason": Code.TRANSPORT}, 0),
        ({}, 1),
        ({"raw_error": "private-diagnostic-must-not-leak"}, 0),
        ({"application_rehearsal_verified": True}, 0),
    ],
)
def test_rehearsal_rejects_inconsistent_or_unallowlisted_reports(changes, exit_code, capfd):
    report = dict(operation="upload", success=True, duration_seconds=0.0)
    report.update(changes)
    with pytest.raises(RemoteError) as failure:
        cli_report(child(report, exit_code), "upload", Deadline(10))
    assert failure.value.code == Code.INTEGRITY
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""


def test_rehearsal_accepts_only_matching_successful_operation():
    report = dict(operation="upload", success=True, duration_seconds=0.0)
    assert cli_report(child(report, 0), "upload", Deadline(10)).success
