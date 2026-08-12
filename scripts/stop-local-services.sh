#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_DIR="$ROOT/.scratch/live-services"

stop_pidfile() {
	local pid_file=$1
	[[ -s $pid_file ]] || return
	local pid
	pid=$(<"$pid_file")
	if kill -0 "$pid" 2>/dev/null; then
		kill "$pid"
		for _ in {1..10}; do
			kill -0 "$pid" 2>/dev/null || break
			sleep 1
		done
	fi
	rm -f "$pid_file"
}

for name in artifact-bridge sandbox grafana-query data-query-planner opensandbox; do
	stop_pidfile "$RUNTIME_DIR/$name.pid"
done
stop_pidfile "$HOME/.pi/agent/gateway.pid"

cd "$ROOT"
docker compose --env-file wferp/test_db/.env -f wferp/test_db/docker-compose.testdb.yml stop
docker compose stop

echo 'Grafana and local services are stopped.'
