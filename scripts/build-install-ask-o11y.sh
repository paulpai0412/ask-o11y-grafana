#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DIR=${ASK_O11Y_BUILD_DIR:-"$ROOT/.scratch/ask-o11y-release-build"}
PLOTLY_PANEL_DIR="$ROOT/grafana-panels/asko11y-plotly-panel"
export PLOTLY_PYTHON=${PLOTLY_PYTHON:-"$ROOT/.venv/bin/python"}
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
git apply "$ROOT/patches/ask-o11y-ml-method-skill.patch"
git apply --unidiff-zero "$ROOT/patches/ask-o11y-data-understanding.patch"
git apply "$ROOT/patches/ask-o11y-upload-capability-closure.patch"
git apply "$ROOT/patches/ask-o11y-upload-session-header.patch"
git apply "$ROOT/patches/ask-o11y-plan-ref-repair.patch"
git apply "$ROOT/patches/ask-o11y-upload-session-attachment.patch"
git apply "$ROOT/patches/ask-o11y-nlap-authority-and-effects.patch"
git apply "$ROOT/patches/ask-o11y-preview-recovery.patch"
git apply "$ROOT/patches/ask-o11y-effect-store.patch"
git apply "$ROOT/patches/ask-o11y-grafana-session-refresh.patch"
git apply "$ROOT/patches/ask-o11y-autonomous-analyst.patch"
git apply "$ROOT/patches/ask-o11y-bounded-autonomy.patch"
git apply "$ROOT/patches/ask-o11y-approval-default.patch"
git apply "$ROOT/patches/ask-o11y-report-dashboard-provenance.patch"
git apply "$ROOT/patches/ask-o11y-semantic-alignment.patch"
git apply "$ROOT/patches/ask-o11y-business-question-binding.patch"
git apply "$ROOT/patches/ask-o11y-writer-gates.patch"
git apply "$ROOT/patches/ask-o11y-selection-closure.patch"
git apply "$ROOT/patches/ask-o11y-delivery-state.patch"
git apply "$ROOT/patches/ask-o11y-input-continuity.patch"
git apply "$ROOT/patches/ask-o11y-delivery-continuity.patch"
git apply "$ROOT/patches/ask-o11y-llm-turn-control.patch"
git apply "$ROOT/patches/ask-o11y-plotly-recovery.patch"
git apply "$ROOT/patches/ask-o11y-novice-report-slimming.patch"
git apply "$ROOT/patches/ask-o11y-report-interface.patch"
git apply "$ROOT/patches/ask-o11y-report-evidence-cursor.patch"
git apply "$ROOT/patches/ask-o11y-prompt-cleanup.patch"
git apply "$ROOT/patches/ask-o11y-native-plotly-delivery.patch"
git apply "$ROOT/patches/ask-o11y-session-tool-history.patch"
git apply "$ROOT/patches/ask-o11y-assessment-status.patch"
npm ci --ignore-scripts
npm run typecheck
npm run build:frontend:prod
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 "$GO_BIN" build -o dist/gpx_consensys-asko11y-app_linux_amd64 ./pkg
node "$ROOT/scripts/stamp-ask-o11y-build.cjs" "$SOURCE_DIR/dist"
EXPECTED_BACKEND_SHA=$(sha256sum dist/gpx_consensys-asko11y-app_linux_amd64 | cut -d ' ' -f 1)
npm --prefix "$PLOTLY_PANEL_DIR" ci
npm --prefix "$PLOTLY_PANEL_DIR" run build
docker run --rm -v grafana_grafana-data:/var/lib/grafana -v "$SOURCE_DIR/dist:/build:ro" -v "$PLOTLY_PANEL_DIR/dist:/plotly-panel:ro" alpine sh -c '
  set -eu
  backup=/var/lib/grafana/plugin-backups/$(date -u +%Y%m%dT%H%M%SZ)-$$
  mkdir -p "$backup"
  for old in /var/lib/grafana/plugins/consensys-asko11y-app.backup* /var/lib/grafana/plugins/asko11y-plotly-panel.backup*; do
    [ ! -d "$old" ] || mv "$old" "$backup/"
  done
  cp -a /var/lib/grafana/plugins/consensys-asko11y-app "$backup/"
  cp -a /var/lib/grafana/plugins/asko11y-plotly-panel "$backup/"
  rm -rf /var/lib/grafana/plugins/consensys-asko11y-app
  mkdir -p /var/lib/grafana/plugins/consensys-asko11y-app
  cp -a /build/. /var/lib/grafana/plugins/consensys-asko11y-app/
  rm -rf /var/lib/grafana/plugins/asko11y-plotly-panel
  mkdir -p /var/lib/grafana/plugins/asko11y-plotly-panel
  cp -a /plotly-panel/. /var/lib/grafana/plugins/asko11y-plotly-panel/
'
docker compose -f "$ROOT/compose.yaml" up -d --force-recreate grafana
for _ in {1..40}; do
  curl -fsS http://127.0.0.1:3000/api/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:3000/api/health >/dev/null
python3 "$ROOT/scripts/check-ask-o11y-effect-store.py"
docker compose -f "$ROOT/compose.yaml" exec -T -e EXPECTED_BACKEND_SHA="$EXPECTED_BACKEND_SHA" grafana sh -c '
  set -eu
  actual=$(sha256sum /var/lib/grafana/plugins/consensys-asko11y-app/gpx_consensys-asko11y-app_linux_amd64)
  [ "${actual%% *}" = "$EXPECTED_BACKEND_SHA" ]
  for proc in /proc/[0-9]*/exe; do
    case "$(readlink "$proc" 2>/dev/null || true)" in
      /var/lib/grafana/plugins/consensys-asko11y-app/gpx_consensys-asko11y-app_linux_amd64)
        actual=$(sha256sum "$proc")
        [ "${actual%% *}" = "$EXPECTED_BACKEND_SHA" ] && exit 0
        exit 1;;
    esac
  done
  echo "Verified backend is not running" >&2
  exit 1
'
echo 'Ask O11y app + Plotly panel installed; running backend matches this build.'
echo 'Python MCP/shared modules and Sandbox image are NOT updated by this installer; coordinated runtime verification is still required.'
