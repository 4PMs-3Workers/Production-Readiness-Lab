"""Reject archived or tampered coverage before combining line-number datasets."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/scripts/vt071_diff_coverage.py"


@pytest.mark.parametrize(
    "failure,message",
    [
        ("missing", "Coverage source metadata is required"),
        ("report", "Coverage JSON does not match"),
        ("source", "Coverage source differs"),
    ],
)
def test_diff_coverage_rejects_unbound_input(tmp_path, failure, message):
    paths = [
        "src/backend/app/api/appointments.py",
        "src/backend/app/repositories/appointments.py",
        "src/backend/app/schemas/appointments.py",
        "src/backend/app/services/appointment_export.py",
    ]
    hashes = {}
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x = 1\n")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = tmp_path / "coverage.json"
    report.write_bytes(b'{"files": {}}')
    if failure != "missing":
        metadata = {
            "coverage_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "file_sha256": hashes,
        }
        Path(str(report) + ".source.json").write_text(json.dumps(metadata), encoding="utf-8")
        if failure == "report":
            report.write_bytes(b'{"files": {}, "changed": true}')
        else:
            (tmp_path / paths[0]).write_bytes(b"y = 1\n")
    outcome = subprocess.run(
        [sys.executable, str(SCRIPT), "--coverage", str(report)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert outcome.returncode == 2
    assert message in outcome.stderr
