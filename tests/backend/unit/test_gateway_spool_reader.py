import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.containers import test_gateway_observability as observability


@pytest.fixture
def local_spool_reader(monkeypatch, tmp_path):
    """Run the real container shell command locally against an owned directory."""
    shell = shutil.which("sh")
    if shell is None and os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "usr/bin/sh.exe"
            if candidate.is_file():
                shell = str(candidate)
    assert shell, "A POSIX shell (such as Git for Windows) is required"
    run = subprocess.run

    def reader(prefix=""):
        def execute(args, **kwargs):
            command = args[-1].replace("/var/log/prl/", "./")
            kwargs["cwd"] = tmp_path
            environment = kwargs["env"].copy()
            environment["PATH"] = str(Path(shell).parent) + os.pathsep + environment["PATH"]
            kwargs["env"] = environment
            return run([shell, "-c", prefix + "\n" + command], **kwargs)

        monkeypatch.setattr(observability.subprocess, "run", execute)
        return observability.gateway_file_records(["docker", "compose"], os.environ.copy())

    return reader


def test_spool_poll_waits_for_record_after_empty_directory(
    local_spool_reader, monkeypatch, tmp_path
):
    local_spool_reader()
    monkeypatch.setattr(
        observability.time,
        "sleep",
        lambda _: (tmp_path / "access.jsonl").write_text('{"request_id":"eventual"}\n'),
    )

    record, _ = observability.wait_file_record(["docker", "compose"], os.environ.copy(), "eventual")

    assert record == {"request_id": "eventual"}


def test_spool_reader_tolerates_last_glob_match_removed_during_read(local_spool_reader, tmp_path):
    (tmp_path / "access.jsonl").write_text('{"request_id":"current"}\n')
    (tmp_path / "access.jsonl.1").write_text('{"request_id":"retired"}\n')

    records, _ = local_spool_reader('cat() { command cat "$@"; rm -f ./access.jsonl.1; }')

    assert records == [{"request_id": "current"}]


def test_spool_reader_tolerates_file_removed_between_test_and_cat(local_spool_reader, tmp_path):
    (tmp_path / "access.jsonl").write_text('{"request_id":"retired"}\n')

    records, output = local_spool_reader('cat() { rm -f "$1"; command cat "$@"; }')

    assert records == []
    assert output == ""


def test_spool_reader_propagates_read_failure_for_existing_file(local_spool_reader, tmp_path):
    (tmp_path / "access.jsonl").write_text('{"request_id":"unreadable"}\n')

    with pytest.raises(subprocess.CalledProcessError):
        local_spool_reader("cat() { return 13; }")


def test_spool_reader_keeps_records_across_files_and_ignores_malformed_lines(
    local_spool_reader, tmp_path
):
    (tmp_path / "access.jsonl").write_text('{"request_id":"current"}\n{"partial":\n')
    (tmp_path / "access.jsonl.1").write_text('{"request_id":"rotated"}\n')

    records, _ = local_spool_reader()

    assert records == [{"request_id": "current"}, {"request_id": "rotated"}]
