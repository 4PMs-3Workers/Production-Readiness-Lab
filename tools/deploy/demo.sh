#!/usr/bin/env bash
# Python owns the portable kernel lock and captures all potentially sensitive output.
set -eu
exec "${PYTHON:-python3}" "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/demo.py" "$@"
