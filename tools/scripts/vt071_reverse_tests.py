"""Replay all export tests in reverse collection order (real isolated MySQL required)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    "tests/backend/integration/test_appointment_export.py",
    "tests/backend/unit/test_appointment_export.py",
]


def main():
    env = dict(os.environ, PYTHONUTF8="1")
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *FILES],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    nodes = [line.strip() for line in collected.stdout.splitlines() if "::" in line]
    if not nodes:
        raise RuntimeError("No tests collected")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--require-mysql",
        "--tb=short",
        *reversed(nodes),
    ]
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    output = ROOT / "docs/assignments/VT-071/evidence/reverse-order.txt"
    output.write_text(
        "Collected nodes in normal order:\n"
        + "\n".join(nodes)
        + "\nExecuted in reverse order:\n"
        + completed.stdout
        + completed.stderr
        + f"\nEXIT_CODE={completed.returncode}\n",
        encoding="utf-8",
    )
    print(completed.stdout)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
