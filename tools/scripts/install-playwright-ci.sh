#!/usr/bin/env bash
set -euo pipefail

# Hosted-runner APT/CDN requests can fail transiently before browser tests start.
# Retry only installation; preserve the last failure after three attempts.
for attempt in 1 2 3; do
  if npx --no-install playwright install --with-deps chromium; then
    exit 0
  else
    install_status=$?
  fi
  if [ "$attempt" -eq 3 ]; then
    exit "$install_status"
  fi
  printf 'Chromium dependency installation failed (attempt %s/3); retrying in 10s.\n' "$attempt"
  sleep 10
done
