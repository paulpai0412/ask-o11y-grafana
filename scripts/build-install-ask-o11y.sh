#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DIR="$ROOT/.scratch/ask-o11y-build"
GO_BIN=${GO_BIN:-"$ROOT/.scratch/go/bin/go"}
BASE_COMMIT=8395ae10c3e38beae56329e4174a14a9a6d4c680

if [[ ! -x $GO_BIN ]]; then
	mkdir -p "$ROOT/.scratch"
	curl -fsSL https://go.dev/dl/go1.26.5.linux-amd64.tar.gz | tar -xz -C "$ROOT/.scratch"
fi
rm -rf "$SOURCE_DIR"
git clone -q https://github.com/Consensys/ask-o11y-plugin.git "$SOURCE_DIR"
cd "$SOURCE_DIR"
git checkout -q "$BASE_COMMIT"
git apply "$ROOT/patches/ask-o11y-dynamic-tools-and-timeout.patch"
git apply "$ROOT/patches/ask-o11y-upload-datasets.patch"
npm ci --ignore-scripts
npm run typecheck
npm run build:frontend:prod
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "$GO_BIN" build -o dist/gpx_consensys-asko11y-app_linux_amd64 ./pkg
docker run --rm -v grafana_grafana-data:/var/lib/grafana -v "$SOURCE_DIR/dist:/build:ro" alpine sh -c '
  rm -rf /var/lib/grafana/plugins/consensys-asko11y-app.backup
  cp -a /var/lib/grafana/plugins/consensys-asko11y-app /var/lib/grafana/plugins/consensys-asko11y-app.backup
  rm -rf /var/lib/grafana/plugins/consensys-asko11y-app
  mkdir -p /var/lib/grafana/plugins/consensys-asko11y-app
  cp -a /build/. /var/lib/grafana/plugins/consensys-asko11y-app/
'
docker compose -f "$ROOT/compose.yaml" restart grafana
for _ in {1..40}; do
	curl -fsS http://127.0.0.1:3000/api/health >/dev/null && break
	sleep 1
done
curl -fsS http://127.0.0.1:3000/api/health >/dev/null
echo 'Ask O11y upload build installed.'
