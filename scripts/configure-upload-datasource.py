#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import urllib.request


def main() -> int:
    base = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
    user = os.environ.get("GRAFANA_USER", "admin")
    password = os.environ.get("GRAFANA_PASSWORD", "admin")
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(base + "/api/datasources/uid/csv-poc", headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            datasource = json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot read Infinity datasource settings") from exc
    hosts = datasource.setdefault("jsonData", {}).setdefault("allowedHosts", [])
    required = ["http://127.0.0.1:8767", "http://127.0.0.1:8772"]
    datasource["jsonData"]["allowedHosts"] = list(dict.fromkeys([*hosts, *required]))
    payload = {key: datasource[key] for key in ["name", "type", "access", "url", "user", "database", "basicAuth", "basicAuthUser", "withCredentials", "isDefault", "jsonData"] if key in datasource}
    update = urllib.request.Request(base + "/api/datasources/uid/csv-poc", data=json.dumps(payload).encode(), method="PUT", headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(update, timeout=10) as response:
            json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot update Infinity datasource settings") from exc
    print("upload datasource hosts configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
