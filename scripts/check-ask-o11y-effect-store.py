#!/usr/bin/env python3
"""Check the actual plugin environment and durable writable mount; no API/model calls."""
import argparse
import json
from pathlib import PurePosixPath
import subprocess


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=20).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="grafana-grafana-1")
    container = parser.parse_args().container
    # Never print or return unrelated process environment entries (which may contain secrets).
    output = docker("exec", container, "sh", "-c", r'''
# This non-secret Compose setting is a canary for accidental all-host forwarding.
if [ -z "${GF_SECURITY_ALLOW_EMBEDDING+x}" ]; then
  printf 'HOST_ENV_MARKER_MISSING=1\n'
fi
for process in /proc/[0-9]*; do
  case "$(readlink "$process/exe" 2>/dev/null)" in
    */gpx_consensys-asko11y-app_linux_amd64)
      printf 'PLUGIN_PID=%s\n' "${process##*/}"
      if tr '\000' '\n' < "$process/environ" | grep -q '^GF_SECURITY_ALLOW_EMBEDDING='; then
        printf 'HOST_ENV_FORWARDED=1\n'
      fi
      tr '\000' '\n' < "$process/environ" | grep -E '^(GF_PLUGIN_ASKO11Y_EFFECT_STORE|ASKO11Y_EFFECT_STORE|GF_PATHS_DATA)=' || true
      ;;
  esac
done
''')
    if output.count("PLUGIN_PID=") != 1:
        raise SystemExit("FAIL: expected exactly one running Ask O11y backend")
    env = dict(line.split("=", 1) for line in output.splitlines())
    if env.get("HOST_ENV_MARKER_MISSING") or env.get("HOST_ENV_FORWARDED"):
        raise SystemExit("FAIL: host-environment isolation check failed or its Compose canary is missing")
    store = env.get("ASKO11Y_EFFECT_STORE") or env.get("GF_PLUGIN_ASKO11Y_EFFECT_STORE")
    if not store and env.get("GF_PATHS_DATA"):
        store = str(PurePosixPath(env["GF_PATHS_DATA"]) / "ask-o11y-operations")
    if not store or not PurePosixPath(store).is_absolute() or ".." in PurePosixPath(store).parts:
        raise SystemExit("FAIL: plugin has no absolute effect store; configure its INI setting")
    try:
        mounts = json.loads(docker("inspect", container, "--format", "{{json .Mounts}}"))
    except (json.JSONDecodeError, subprocess.SubprocessError) as exc:
        raise SystemExit("FAIL: could not inspect persistent mounts") from exc
    covering = [m for m in mounts if PurePosixPath(store).is_relative_to(m["Destination"])]
    mount = max(covering, key=lambda m: len(m["Destination"]), default={})
    if mount.get("Type") not in {"volume", "bind"} or not mount.get("RW"):
        raise SystemExit("FAIL: effect store is not backed by a writable persistent mount")
    # Match effective UID/GID and fail closed if Docker cannot reproduce the group context.
    status = docker("exec", container, "sh", "-c",
                    'grep -E "^(Uid|Gid|Groups):" "/proc/$1/status"', "sh", env["PLUGIN_PID"])
    identity = {key: value.split() for key, value in (line.split(":", 1) for line in status.splitlines())}
    if (len(identity.get("Uid", [])) != 4 or len(identity.get("Gid", [])) != 4
            or "Groups" not in identity or not all(v.isdigit() for values in identity.values() for v in values)):
        raise SystemExit("FAIL: cannot determine plugin OS identity")
    uid, gid = identity["Uid"][1], identity["Gid"][1]
    exec_args = ("exec", "--user", f"{uid}:{gid}", container)
    groups = docker(*exec_args, "id", "-G").split()
    if set(groups) != set(identity["Groups"]) | {gid}:
        raise SystemExit("FAIL: write probe groups differ from plugin; cannot attest permissions")
    # Only the temporary probe file is removed, never operation receipts.
    docker(*exec_args, "sh", "-c",
           'set -eu; umask 077; mkdir -p "$1"; chmod 700 "$1"; probe=$(mktemp "$1/.write-check.XXXXXX"); rm -- "$probe"', "sh", store)
    print(f"PASS: Ask O11y PID {env['PLUGIN_PID']} receives {store}; persistent and writable as {uid}:{gid}; host-env canary absent")


if __name__ == "__main__":
    main()
