#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBS="$REPO_ROOT/observability"

if [[ ! -f "$OBS/.env" ]]; then
  echo "Missing $OBS/.env"
  echo "Create it first: cp $OBS/.env.example $OBS/.env"
  exit 1
fi

cd "$OBS"
docker compose up -d

docker compose ps
