#!/usr/bin/env python3
"""Configure Ask O11y for adaptive high-level analysis MCP tools.

Default mode is safe: build/validate the settings payload without contacting
Grafana. Use --apply with explicit Grafana auth env vars to update plugin
settings.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PLUGIN_ID = "consensys-asko11y-app"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / ".scratch" / "poc" / "ask-o11y-workflow-tools-settings.json"

SYSTEM_PROMPT = """For analysis requests, act as an adaptive planner using only the currently enabled high-level tool schemas, the user's intent, authorized metadata, and intermediate results.

Before execution:
- If the intent, desired outcome, or constraints are ambiguous, ask a focused clarification and call no execution tool.
- You may use read-only capability and datasource metadata tools and may create a safe query plan before confirmation. When metadata declares a validity/quality companion for a selected measurement, keep that companion in the query plan but do not use it as a model feature. Do not execute a datasource query, run an analysis method, or mutate Grafana yet.
- Present an Analysis Preview containing the objective, candidate/selected datasource, fields and any target/features, requested visualizations, assumptions, risks, and expected artifacts. Include explicit `方法選擇理由` and `Validation / Evaluation 計畫` sections: explain why each proposed method fits the requested objective and available field types, and name concrete data-quality/precondition checks plus evaluation metrics or output-integrity checks (or explain why predictive evaluation is not applicable). Assumptions or generic limitations alone do not satisfy validation/evaluation. Then stop and ask the user to confirm or revise it.

After explicit confirmation in the same conversation:
- Select each next tool from its schema and the current results. There is no fixed workflow, mandatory next_step chain, method sequence, target, feature set, or panel template.
- Call only capabilities required by the confirmed intent. Never call unrelated analysis methods "for completeness".
- Pass opaque artifact refs and explicit scalar options; never place raw frames, full method results, full AnalysisResult payloads, physical paths, credentials, or secrets in model-visible arguments or prose.
- Reuse an opaque artifact ref only when it came from a successful tool result in this session or the user's explicit input; copy it character-for-character and never reconstruct, shorten, extend, or guess it.
- Inspect `ok` after every tool. On any `isError=true`, `ok=false`, non-recoverable error, clarification, rejected approval, invalid ref/field, or failed method, do not retry another tool in that run: stop observed execution and report it without claiming success.
- If intermediate evidence requires a material change to datasource, fields, methods, evaluation, or outputs, present a revised preview and wait for confirmation before continuing.
- Treat query planning as plan-only, datasource execution as Grafana-read-only, domain analysis as artifact-only deterministic computation, and rendering as an approval-gated Grafana write. The user's explicit confirmation of a preview that includes a dashboard authorizes that planned mutation: after AnalysisResult, call the Renderer preparation tool and immediately call its write tool with the returned short-lived one-time approval_ref; do not ask for a second chat confirmation. The Ask O11y host independently gates the write tool and must obtain its own user approval. Never invent, reconstruct, reuse, or omit approval_ref.
- A VisualizationSpec or AnalysisResult is not a rendered chart. Claim that a chart/dashboard exists only after a successful Renderer write returns its Grafana URL. If the confirmed request produces separate AnalysisResults that all need visualization, render each required result and include every returned URL; never silently omit an output promised in the preview.
- Preserve returned safety limitations and dashboard URLs in the final explanation.
"""

CAPABILITY_CONFIG = ROOT / "config" / "adaptive-mcp-capabilities.json"


def load_server_specs() -> list[dict[str, Any]]:
    try:
        raw = json.loads(CAPABILITY_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load adaptive MCP capability config: {exc}") from exc
    servers = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, list) or len(servers) != 5 or any(not isinstance(item, dict) for item in servers):
        raise SystemExit("adaptive MCP capability config must define exactly five trust-seam servers")
    required = {"id", "name", "url_env", "local_url", "enabled_tools", "disabled_tools"}
    for server in servers:
        if set(server) != required or not isinstance(server["enabled_tools"], list) or not isinstance(server["disabled_tools"], list):
            raise SystemExit(f"invalid adaptive MCP capability entry: {server.get('id')}")
    ids = [str(item["id"]) for item in servers]
    if ids != ["data-query-planner", "grafana-query", "engineering-analysis", "finance-analysis", "grafana-renderer"]:
        raise SystemExit(f"capability config changed required trust seams: {ids}")
    return servers


SERVER_SPECS = load_server_specs()


def tool_key(server_id: str, tool_name: str) -> str:
    return f"{server_id}_{tool_name}"


def server_url(spec: dict[str, Any], use_local_defaults: bool) -> str:
    value = os.environ.get(str(spec["url_env"]), "")
    if value:
        return value
    if use_local_defaults:
        return str(spec["local_url"])
    raise SystemExit(f"{spec['url_env']} is required (or pass --local-defaults for local development)")


def tool_selections(spec: dict[str, Any]) -> dict[str, bool]:
    selections: dict[str, bool] = {}
    for name in spec["enabled_tools"]:
        selections[name] = True
        selections[tool_key(str(spec["id"]), str(name))] = True
    for name in spec["disabled_tools"]:
        selections[name] = False
        selections[tool_key(str(spec["id"]), str(name))] = False
    return selections


def build_servers(use_local_defaults: bool) -> list[dict[str, Any]]:
    return [
        {
            "id": spec["id"],
            "name": spec["name"],
            "url": server_url(spec, use_local_defaults),
            "type": "streamable-http",
            "enabled": True,
            "trusted": True,
            "headers": {"Authorization": "", "X-Grafana-Org-Id": "", "X-Grafana-User": ""},
            "toolSelections": tool_selections(spec),
        }
        for spec in SERVER_SPECS
    ]


def build_json_data(existing: dict[str, Any], use_local_defaults: bool) -> dict[str, Any]:
    json_data = dict(existing)
    json_data.update(
        {
            "mcpServers": build_servers(use_local_defaults),
            "trustedMCPServers": {str(spec["id"]): True for spec in SERVER_SPECS},
            "useBuiltInMCP": False,
            "builtInMCPToolSelections": {},
            "defaultSystemPrompt": SYSTEM_PROMPT,
            "maxParallelToolCalls": 1,
            "approvalPolicy": json_data.get("approvalPolicy", "approval-gated-writes"),
        }
    )
    return json_data


def validate_payload(payload: dict[str, Any]) -> None:
    json_data = payload.get("jsonData")
    if not isinstance(json_data, dict):
        raise SystemExit("payload.jsonData is required")
    built_in_mcp = json_data.get("useBuiltInMCP")
    if not isinstance(built_in_mcp, bool) or built_in_mcp:
        raise SystemExit("useBuiltInMCP must be false for the high-level main flow")
    servers = json_data.get("mcpServers")
    if not isinstance(servers, list) or len(servers) != len(SERVER_SPECS):
        raise SystemExit("payload must contain exactly the high-level workflow-node servers")
    expected_ids = {str(spec["id"]) for spec in SERVER_SPECS}
    actual_ids = {str(server.get("id")) for server in servers}
    if actual_ids != expected_ids:
        raise SystemExit(f"unexpected MCP servers: {sorted(actual_ids)}")
    for spec in SERVER_SPECS:
        server = next(item for item in servers if item.get("id") == spec["id"])
        headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
        if set(headers) != {"Authorization", "X-Grafana-Org-Id", "X-Grafana-User"} or any(headers.values()):
            raise SystemExit(f"secure MCP header placeholders missing: {spec['id']}")
        selections = server.get("toolSelections") if isinstance(server.get("toolSelections"), dict) else {}
        for name in spec["enabled_tools"]:
            direct_enabled = selections.get(name)
            prefixed_enabled = selections.get(tool_key(str(spec["id"]), str(name)))
            if not isinstance(direct_enabled, bool) or not direct_enabled or not isinstance(prefixed_enabled, bool) or not prefixed_enabled:
                raise SystemExit(f"enabled tool missing from selections: {spec['id']} {name}")
        for name in spec["disabled_tools"]:
            direct_disabled = selections.get(name)
            prefixed_disabled = selections.get(tool_key(str(spec["id"]), str(name)))
            if not isinstance(direct_disabled, bool) or direct_disabled or not isinstance(prefixed_disabled, bool) or prefixed_disabled:
                raise SystemExit(f"low-level tool not disabled: {spec['id']} {name}")
    prompt = str(json_data.get("defaultSystemPrompt", ""))
    for required in ["Analysis Preview", "explicit confirmation", "There is no fixed workflow", "opaque artifact refs", "approval-gated Grafana write"]:
        if required not in prompt:
            raise SystemExit(f"adaptive system prompt missing: {required}")


def auth_headers() -> dict[str, str]:
    token = os.environ.get("GRAFANA_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    user = os.environ.get("GRAFANA_USER", "")
    password = os.environ.get("GRAFANA_PASSWORD", "")
    if user and password:
        encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    raise SystemExit("GRAFANA_TOKEN or both GRAFANA_USER/GRAFANA_PASSWORD are required for --apply")


def secure_mcp_headers() -> dict[str, str]:
    token = os.environ.get("MCP_SHARED_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("MCP_SHARED_TOKEN with at least 32 characters is required for --apply")
    org_id = os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1")
    user_id = os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y")
    secure: dict[str, str] = {}
    for spec in SERVER_SPECS:
        prefix = f"mcpServerHeader.{spec['id']}."
        secure[prefix + "Authorization"] = f"Bearer {token}"
        secure[prefix + "X-Grafana-Org-Id"] = org_id
        secure[prefix + "X-Grafana-User"] = user_id
    return secure


def apply_settings(grafana_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = grafana_url.rstrip("/") + f"/api/plugins/{PLUGIN_ID}/settings"
    headers = {"Content-Type": "application/json", **auth_headers()}
    request_payload = {**payload, "secureJsonData": secure_mcp_headers()}
    req = urllib.request.Request(url, data=json.dumps(request_payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"Grafana settings update failed HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Grafana settings update failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-defaults", action="store_true", help="Use 127.0.0.1 MCP URLs for local development when URL env vars are unset.")
    parser.add_argument("--apply", action="store_true", help="POST the settings to Grafana; requires explicit Grafana auth env vars.")
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", ""))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    payload = {"enabled": True, "pinned": True, "jsonData": build_json_data({}, args.local_defaults)}
    validate_payload(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.self_check:
        print(json.dumps({"ok": True, "settings_payload": str(args.out.relative_to(ROOT)), "servers": [spec["id"] for spec in SERVER_SPECS], "built_in_mcp": False}, ensure_ascii=False, indent=2))
        return 0
    if args.apply:
        if not args.grafana_url:
            raise SystemExit("--grafana-url or GRAFANA_URL is required for --apply")
        result = apply_settings(args.grafana_url, payload)
        print(json.dumps({"ok": True, "settings_payload": str(args.out), "grafana_response": result}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": True, "dry_run": True, "settings_payload": str(args.out), "apply": "rerun with --apply and explicit Grafana auth env vars"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
