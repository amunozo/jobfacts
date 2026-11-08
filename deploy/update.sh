#!/usr/bin/env bash
# Update the server to the latest code: pull, rebuild the image, migrate, restart what changed.
# Run on the server:  ~/jobfacts/deploy/update.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

dc() {
	if docker info >/dev/null 2>&1; then "$ROOT/deploy/dc" "$@"; else sudo "$ROOT/deploy/dc" "$@"; fi
}

git pull --ff-only
dc build --pull
dc pull --ignore-buildable
# Migrate with the new image BEFORE the new code serves: if this fails, the old containers
# keep running (set -e stops here). Migrations are meant to be additive and safe to repeat.
dc run --rm -T app jobfacts migrate
dc up -d --wait --wait-timeout 180
# Drop images that nothing uses any more (every build leaves the old one behind).
if docker info >/dev/null 2>&1; then docker image prune -f >/dev/null; else sudo docker image prune -f >/dev/null; fi
dc ps
echo "update: done. If deploy/*.service or deploy/*.timer changed, run deploy/setup.sh again."
