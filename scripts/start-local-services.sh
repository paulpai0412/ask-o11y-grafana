#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_DIR="$ROOT/.scratch/live-services"
GATEWAY_CLI=${PI_GATEWAY_CLI:-"$HOME/.pi/agent/npm/node_modules/pi-gateway/dist/cli.js"}

cd "$ROOT"
set -a
. ./.env
set +a
: "${MCP_SHARED_TOKEN:?MCP_SHARED_TOKEN is required in .env}"
: "${ANALYSIS_SERVICE_ORG_ID:?ANALYSIS_SERVICE_ORG_ID is required in .env}"
: "${ANALYSIS_SERVICE_USER_ID:?ANALYSIS_SERVICE_USER_ID is required in .env}"
: "${SANDBOX_IMAGE:?SANDBOX_IMAGE is required in .env}"
: "${SANDBOX_RUNTIME_CLASS:?SANDBOX_RUNTIME_CLASS is required in .env}"
: "${SANDBOX_SERVER_CONFIG:?SANDBOX_SERVER_CONFIG is required in .env}"
: "${U1_OPERATING_CSV_URL:?U1_OPERATING_CSV_URL is required in .env}"

mkdir -p "$RUNTIME_DIR"
# MCP-only deploys preserve existing databases and their data.
if [[ ${1:-} != --mcp-only ]]; then
	docker compose up -d
	docker compose --env-file wferp/test_db/.env -f wferp/test_db/docker-compose.testdb.yml up -d
	for _ in {1..40}; do
		[[ $(docker inspect wferp-mssql-test --format '{{.State.Health.Status}}' 2>/dev/null || true) == healthy ]] && break
		sleep 3
	done
	[[ $(docker inspect wferp-mssql-test --format '{{.State.Health.Status}}' 2>/dev/null || true) == healthy ]] || {
		echo 'WFERP test DB did not become healthy' >&2
		exit 1
	}
	set -a
	. wferp/test_db/.env
	set +a
	docker exec -i wferp-mssql-test /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -i /init/01_create_wferp_test.sql >/dev/null
fi

# User units (with loginctl linger enabled) survive logout and start after WSL boots.
# Keep imported Python modules fresh on deploy without unmanaged duplicate workers.
systemctl --user daemon-reload
systemctl --user start grafana-opensandbox.service
systemctl --user restart \
	grafana-mcp@ontology-mcp.service \
	grafana-mcp@data-query-planner-mcp.service \
	grafana-mcp@grafana-query-mcp.service \
	grafana-mcp@sandbox-analysis-mcp.service \
	grafana-mcp@artifact-bridge-mcp.service

if ! curl -fsS http://127.0.0.1:4000/healthz >/dev/null 2>&1; then
	[[ -f $GATEWAY_CLI ]] || {
		echo "pi-gateway CLI not found: $GATEWAY_CLI" >&2
		exit 1
	}
	nohup node "$GATEWAY_CLI" >>"$RUNTIME_DIR/pi-gateway.log" 2>&1 &
fi

for endpoint in 3000 8080 8768 8771 8772 8773 8777 4000 14334; do
	for _ in {1..30}; do
		(echo >/dev/tcp/127.0.0.1/"$endpoint") >/dev/null 2>&1 && break
		sleep 1
	done
	(echo >/dev/tcp/127.0.0.1/"$endpoint") >/dev/null 2>&1 || {
		echo "service on :$endpoint did not start" >&2
		exit 1
	}
done

uv run python scripts/configure-upload-datasource.py

echo 'Grafana and local services are running.'
