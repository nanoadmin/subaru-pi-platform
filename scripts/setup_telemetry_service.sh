#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TPL="$REPO_ROOT/telemetry/systemd/subaru-telemetry.service.template"
DST="/etc/systemd/system/subaru-telemetry.service"

if [[ ! -f "$TPL" ]]; then
  echo "Template not found: $TPL" >&2
  exit 1
fi

echo "Installing systemd unit..."
sudo sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$TPL" | sudo tee "$DST" >/dev/null

echo "Creating telemetry runtime dirs..."
mkdir -p "$REPO_ROOT/telemetry/runtime" "$REPO_ROOT/telemetry/logs"

echo "Installing logrotate config..."
sudo sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$REPO_ROOT/telemetry/logrotate/subaru-telemetry" | sudo tee /etc/logrotate.d/subaru-telemetry >/dev/null

echo "Reloading and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable --now subaru-telemetry.service

sudo systemctl status subaru-telemetry.service --no-pager -n 25
