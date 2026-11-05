"""Sourced MySQL initializers must preserve their entrypoint shell's behavior."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.rollout.test_mysql_bootstrap import write_runtime_user_script


@pytest.mark.parametrize("sql_exit", [0, 9])
def test_runtime_initializer_preserves_entrypoint_options_and_sql_failures(tmp_path, sql_exit):
    if os.name == "nt":
        git = shutil.which("git")
        shell = Path(git).resolve().parents[1] / "usr/bin/bash.exe" if git else None
        assert shell and shell.is_file(), "Git Bash is required for shell contract tests"
    else:
        shell = shutil.which("bash")
        assert shell, "Bash is required for shell contract tests"
    write_runtime_user_script(tmp_path / "10-runtime-user.sh")
    completed = subprocess.run(
        [
            str(shell),
            "-e",
            "-c",
            'mysql() { return "$BOOTSTRAP_SQL_EXIT"; }\n'
            "original_flags=$-\n"
            ". ./10-runtime-user.sh\n"
            '[ "$-" = "$original_flags" ]\n'
            "unset MYSQL_ONETIME_PASSWORD\n"
            '[ -z "$MYSQL_ONETIME_PASSWORD" ]\n'
            'printf "ready-for-final-server\\n"\n',
        ],
        cwd=tmp_path,
        env=dict(
            os.environ,
            MYSQL_ROOT_PASSWORD="test-only-root",
            PRL_RUNTIME_PASSWORD="test-only-runtime",
            BOOTSTRAP_SQL_EXIT=str(sql_exit),
        ),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == sql_exit, completed.stderr
    assert completed.stdout == ("ready-for-final-server\n" if sql_exit == 0 else "")
