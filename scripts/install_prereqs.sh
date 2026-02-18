#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] Updating apt index..."
sudo apt update

echo "[2/4] Installing system packages..."
sudo apt install -y \
  git curl ca-certificates gnupg lsb-release \
  python3 python3-pip python3-venv \
  mosquitto mosquitto-clients \
  jq

echo "[3/4] Installing Docker if missing..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

sudo usermod -aG docker "$USER"

echo "[4/4] Enabling mosquitto..."
sudo systemctl enable --now mosquitto

echo "Done. Re-login (or reboot) so docker group applies in new shells."
