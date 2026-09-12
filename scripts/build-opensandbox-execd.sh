#!/usr/bin/env bash
# Rebuild the configured execd version with the request-completion fix; no deployment.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-"$ROOT/.scratch/opensandbox-execd-build"}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
GO=${GO:-go}
REV=34653f7a70da87029abb40624f1de8dfee3f1576
BASE=opensandbox/execd@sha256:1dc98c7de10b9a73450ac75aa0f200ad7972f2c40f5225f6a8998e166b45d6dd
WORK=$(mktemp -d "$OUT/source.XXXXXX")
curl -fsSL --max-time 120 "https://codeload.github.com/opensandbox-group/OpenSandbox/tar.gz/$REV" -o "$WORK/source.tar.gz"
printf '%s  %s\n' 49e7e3e4c8aa8e96d05c2765e2458c27e25cc7de68830095732baf02c37646e1 "$WORK/source.tar.gz" | sha256sum -c -
tar -xzf "$WORK/source.tar.gz" -C "$WORK"
cd "$WORK/OpenSandbox-$REV"
patch --batch --fuzz=0 -p1 <"$ROOT/patches/opensandbox-execd-completion.patch"
cd components/execd
export GOTOOLCHAIN=local GOMAXPROCS=2 CGO_ENABLED=0
"$GO" version
"$GO" test -mod=readonly -p 2 ./pkg/jupyter/execute ./pkg/runtime ./pkg/web/controller -count=1 -timeout=120s
"$GO" build -mod=readonly -p 2 -trimpath -buildvcs=false \
  -ldflags "-buildid= -B none -X github.com/alibaba/opensandbox/internal/version.Version=v1.0.21-local-completion -X github.com/alibaba/opensandbox/internal/version.GitCommit=$REV+completion-fix" \
  -o "$OUT/execd" ./main.go
printf 'FROM %s\nCOPY execd /execd\n' "$BASE" >"$OUT/Dockerfile"
printf '*\n!Dockerfile\n!execd\n' >"$OUT/.dockerignore"
docker build --network=none --pull=false -t ask-o11y-execd:completion-fix "$OUT"
sha256sum "$OUT/execd" "$ROOT/patches/opensandbox-execd-completion.patch"
docker image inspect ask-o11y-execd:completion-fix --format '{{.Id}}'
# Pin the printed image ID in runtime.execd_image only as part of an approved rollout.
