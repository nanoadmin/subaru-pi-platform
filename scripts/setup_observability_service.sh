#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TPL="$REPO_ROOT/observability/systemd/subaru-observability.service.template"
DST="/etc/systemd/system/subaru-observability.service"

if [[ ! -f "$TPL" ]]; then
  echo "Template not found: $TPL" >&2
  exit 1
fi
if [[ ! -f "$REPO_ROOT/observability/.env" ]]; then
  echo "Missing $REPO_ROOT/observability/.env"
  echo "Create it first: cp $REPO_ROOT/observability/.env.example $REPO_ROOT/observability/.env"
  exit 1
fi

echo "Installing observability systemd unit..."
sudo sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$TPL" | sudo tee "$DST" >/dev/null

echo "Reloading and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable --now subaru-observability.service

sudo systemctl status subaru-observability.service --no-pager -n 40
