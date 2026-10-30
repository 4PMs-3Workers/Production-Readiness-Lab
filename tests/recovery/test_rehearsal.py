import os

import pytest
from tools.backup.evidence import write_report
from tools.backup.rehearsal import ROOT, run


def test_required_real_application_restore_rehearsal(tmp_path):
    if os.environ.get("RUN_BACKUP_REHEARSAL") != "1":
        pytest.fail(
            "RUN_BACKUP_REHEARSAL=1 and local Docker/Compose are required; acceptance cannot skip"
        )
    report = run(tmp_path / "private")
    assert report["accepted"] is True and all(report["smoke"].values())
    output = ROOT / "build/recovery-review/rehearsal.json"
    write_report(report, output)
