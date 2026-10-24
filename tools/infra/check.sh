#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
if [[ -x "$repo_root/.venv/Scripts/python.exe" ]]; then
    python_bin="$repo_root/.venv/Scripts/python.exe"
elif [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
else
    printf '%s\n' 'Task 6 validation requires the repository virtual-environment Python.' >&2
    exit 2
fi

exec "$python_bin" "$repo_root/tools/infra/check_telemetry.py" "$@"
