#!/bin/sh
set -eu

SOURCE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
PACKAGE_DIR="$SOURCE_ROOT/tools/infra/offhost"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer with sudo." >&2
  exit 2
fi
if ! command -v python3.11 >/dev/null 2>&1; then
  echo "Python 3.11 is required." >&2
  exit 2
fi

if ! id prlmonitor >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/prl-offhost --shell /usr/sbin/nologin prlmonitor
fi
install -d -o root -g root -m 0755 /opt/prl-offhost
python3.11 -m venv /opt/prl-offhost/venv
/opt/prl-offhost/venv/bin/pip install --no-deps "$PACKAGE_DIR"
install -d -o root -g prlmonitor -m 0750 /etc/prl-offhost
install -o root -g prlmonitor -m 0640 "$PACKAGE_DIR/config.example.toml" \
  /etc/prl-offhost/probe.toml.example
install -o root -g root -m 0644 "$PACKAGE_DIR/prl-offhost-monitor.service" \
  /etc/systemd/system/prl-offhost-monitor.service
install -o root -g root -m 0644 "$PACKAGE_DIR/prl-offhost-test-receiver.service" \
  /etc/systemd/system/prl-offhost-test-receiver.service
systemctl daemon-reload
printf '%s\n' \
  "Package installed. Copy and edit /etc/prl-offhost/probe.toml.example as /etc/prl-offhost/probe.toml," \
  "set explicit deployment endpoints, then run: systemctl enable --now prl-offhost-monitor.service"
