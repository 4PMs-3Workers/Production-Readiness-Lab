"""Core transaction helpers must import before any HTTP API initialization."""

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def test_transaction_helpers_import_without_initializing_api():
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, PYTHONPATH=str(root / "src/backend"))
    code = dedent("""
        import sys
        from app.core.transactions import (
            CommitOutcomeUnknown,
            commit_safely,
            invalidate,
            mysql_error_code,
            read_transaction,
            rollback,
        )
        assert "app.api" not in sys.modules
        """)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
