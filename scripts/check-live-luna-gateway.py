#!/usr/bin/env python3
"""Verify live Grafana LLM routing, pi-gateway model exposure, and fresh Ask O11y evidence."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".scratch" / "poc" / "luna-gateway-compatibility.json"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
GATEWAY_URL = os.environ.get("PI_GATEWAY_URL", "http://127.0.0.1:4000").rstrip("/")
MODEL = "openai-codex/gpt-5.6-luna"


def fetch_json(url: str, grafana_auth: bool = False) -> dict[str, Any]:
    headers = {}
    if grafana_auth:
        token = base64.b64encode(f"{os.environ.get('GRAFANA_USER', 'admin')}:{os.environ.get('GRAFANA_PASSWORD', 'admin')}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except (urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot fetch {url}") from exc


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {path}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    llm = fetch_json(GRAFANA_URL + "/api/plugins/grafana-llm-app/settings", grafana_auth=True)
    mapping = llm.get("jsonData", {}).get("models", {}).get("mapping", {})
    provider = llm.get("jsonData", {}).get("provider")
    gateway_url = llm.get("jsonData", {}).get("openAI", {}).get("url")
    models = fetch_json(GATEWAY_URL + "/v1/models").get("data") or []
    model_ids = [item.get("id") for item in models if isinstance(item, dict)]
    correlation = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-correlation-preview-e2e.json")
    dynamic = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-dynamic-engineering-e2e.json")
    negatives = load_json(ROOT / ".scratch" / "poc" / "ask-o11y-negative-e2e.json")
    run_ids = [correlation["preview"]["runId"], correlation["execution"]["runId"]]
    for case in dynamic["cases"]:
        run_ids.extend([case["preview"]["runId"], case["execution"]["runId"]])
    for case in negatives["cases"].values():
        run_ids.extend(str(case[key]) for key in ["runId", "preview_run", "execution_run", "rejection_run"] if case.get(key))
    require(mapping.get("base") == MODEL and mapping.get("large") == MODEL, f"Grafana model mapping mismatch: {mapping}")
    require(provider == "custom" and gateway_url == "http://localhost:4000", f"Grafana gateway route mismatch: {provider}/{gateway_url}")
    require(MODEL in model_ids, f"gateway does not expose {MODEL}")
    evidence_flags = [correlation.get("ok"), dynamic.get("ok"), negatives.get("ok")]
    require(all(isinstance(flag, bool) and flag for flag in evidence_flags) and all(bool(run_id) for run_id in run_ids), "fresh Ask O11y evidence incomplete")
    evidence = {"ok": True, "model": MODEL, "grafana_mapping": mapping, "provider": provider, "grafana_gateway_url": gateway_url, "gateway_model_count": len(model_ids), "gateway_exposes_model": True, "fresh_run_ids": run_ids}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
