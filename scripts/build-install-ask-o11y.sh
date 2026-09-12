#!/usr/bin/env bash
# Kept as the existing entrypoint; this command now builds only, never installs.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DIR="$ROOT/ask-o11y"
GO_BIN=${GO_BIN:-"$ROOT/.scratch/go/bin/go"}

if [[ $# != 0 ]]; then
  echo 'Usage: scripts/build-install-ask-o11y.sh (local build only; deployment is separate)' >&2
  exit 2
fi
if [[ ! -x "$GO_BIN" ]]; then
  echo 'Go is not installed. Set GO_BIN to an existing Go executable.' >&2
  exit 1
fi
if [[ ! -d "$SOURCE_DIR/node_modules" ]]; then
  echo 'Dependencies are missing. Run npm ci --ignore-scripts in ask-o11y/ first.' >&2
  exit 1
fi

cd "$SOURCE_DIR"
npm run typecheck
# Reused dependencies must not reuse another checkout's webpack module cache.
npm run build:frontend:prod -- --no-cache
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "$GO_BIN" build -o dist/gpx_consensys-asko11y-app_linux_amd64 ./pkg
node "$ROOT/scripts/stamp-ask-o11y-build.cjs" "$SOURCE_DIR/dist"
echo "Built $SOURCE_DIR/dist from maintained source. No services, settings or plugins were changed."
