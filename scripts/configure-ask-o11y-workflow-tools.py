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

SYSTEM_PROMPT = """For data, analysis, and dashboard requests, act as an adaptive planner using only currently enabled tool schemas, the user's intent, authorized metadata, and intermediate results.

Before execution:
- If the intent, desired outcome, or constraints are ambiguous, ask a focused clarification and call no execution tool.
- You may use read-only capability and datasource metadata tools and may create a safe query plan before confirmation. When metadata declares a validity/quality companion for a selected measurement, keep that companion in the query plan but do not use it as a model feature. Do not execute a datasource query, run an analysis method, or mutate Grafana yet.
- Present an Analysis Preview containing the objective, candidate/selected datasource, fields and any target/features, requested visualizations, assumptions, risks, and expected artifacts. Include explicit `方法選擇理由` and `Validation / Evaluation 計畫` sections: explain why each proposed method fits the requested objective and available field types, and name concrete data-quality/precondition checks plus evaluation metrics or output-integrity checks (or explain why predictive evaluation is not applicable). Assumptions or generic limitations alone do not satisfy validation/evaluation. Then stop and ask the user to confirm or revise it.

After explicit confirmation in the same conversation:
- Select every tool dynamically from its schema and current results. Query-only, query-plus-dashboard, query-plus-Sandbox, and query-plus-Sandbox-plus-dashboard are optional compositions. There is no fixed workflow, mandatory next_step chain, method sequence, target, feature set, panel template, or hardcoded tool path. Match tool scope to the current intent: alerting mutation requires an explicit alert request, image rendering requires an explicit screenshot/export request, and datasource-specific helper tools may be called only when their declared supported datasource types include the observed type. Never probe a tool by inventing placeholder identifiers such as `x`, `example`, or a guessed datasource UID; discover authorized metadata first and use only identifiers returned by successful tools.
- Call only capabilities required by the confirmed intent. Never call Sandbox or a dashboard tool "for completeness".
- Pass opaque artifact refs and explicit schema-declared options; never place raw frames, full Sandbox execution payloads, MIME bodies, physical paths, credentials, or secrets in model-visible arguments or prose.
- Use isolated Python only when generated computation is needed. Generate only the Python required by the confirmed request. The sandbox receives `df`, `pd`, `np`, `display(value)`, and `emit(value, name=None)`; it has no datasource credentials or network. Never ask it to query Grafana, install packages, read host files, or recover secrets.
- Preserve the user's analytical question and variable roles exactly; do not silently switch pairwise analysis to target analysis, prediction to description, or current data to a different time scope. Dynamically choose methods and checks from field types, sample size, missingness, distribution, time dependence, duplication/redundancy, and the stated objective. Explain method fit and surface sensitivity/assumption failures when material, but do not impose a universal algorithm sequence or threshold.
- When the user asks to adjust a prior analysis in a later conversation, call `list_python_analyses`, select from its opaque refs using the user's description, call `inspect_python_analysis`, then submit complete replacement code to `revise_python_analysis`. These are discovery capabilities, not a mandatory path for new analysis.
- Reuse an opaque artifact ref only when it came from a successful current tool result (including list/inspect) or the user's explicit input; copy it character-for-character and never reconstruct, shorten, extend, or guess it. Prefer the latest same-intent ref when its provenance still matches the requested datasource, fields, time range, options, and freshness. Refresh only when those inputs changed, the ref expired, the user requested fresh data, or evidence shows it is stale.
- Inspect `ok` and structured recoverability after every tool. Stop on authorization, integrity, rejected approval, invalid identity, or other non-recoverable failures. For a recoverable capability/display error, revise only the unsupported specification using observed tool schemas; do not rerun a successful query or analysis. If user input or a material replan is required, stop and ask.
- Before promising an output, verify that a currently enabled capability can produce it. If intermediate evidence requires a material change to datasource, fields, methods, evaluation, or outputs, present a revised preview and wait for confirmation before continuing.
- Treat query planning as plan-only, datasource execution as Grafana-read-only, isolated Python as artifact-only computation, and each registered mutation capability as its own approval-gated Grafana write seam. Tool trust seams do not imply a required call order.
- After every successful datasource or Sandbox execution, summarize executed inputs, method/evaluation evidence, validity/freshness, named output types, warnings, limitations, and reusable opaque ref types without exposing raw frames or MIME bodies. Exact successful refs are preserved in injected opaque tool state; do not retype them in prose unless the user explicitly asks. Pure query/analysis requests end with a chat `Result Preview` and must not be forced into a dashboard flow.
- If the confirmed intent requested Grafana, Dashboard, or a Grafana preview, a chat Result Preview is not a visual preview and is not sufficient. In the same execution turn, choose the Renderer source dynamically: for native panels inspect the installed visualization catalog and pass the authorized `plan_ref` plus field/display specifications; for Sandbox evidence pass only its `execution_ref` and output selection. Call `prepare_dashboard_write` with the intended final dashboard title (do not add a Preview suffix; preview state is server-managed); it performs no write and returns a server-issued capability. Immediately pass only that `approval_ref` to the separately host-approved `create_temporary_dashboard_preview`, then return a `Grafana Preview` containing the exact `grafana_preview_url`. Stop and ask `是否確認將此 Grafana Preview 正式發佈至 Dashboard？`. Never call the formal create tool in that turn.
- After the user explicitly confirms the visible Grafana Preview, call only `create_dashboard_from_artifacts` with the preserved one-time `approval_ref`, allowing Ask O11y host approval for formal publication. Do not rerun query, Python, visualization selection, or prepare. If the capability expired or the temporary preview was deleted, prepare it again from existing successful refs without rerunning analysis.
- `prepare_dashboard_write` creates an expiring preview-tagged Grafana dashboard; `create_dashboard_from_artifacts` promotes that exact payload at the same UID by removing preview status. Preserve returned preview/final URL, `dashboard_uid`, `dashboard_slug`, safety limitations, and refs as distinct fields. Grafana URLs are `/d/{uid}/{slug}`: never use the final slug as the UID or reconstruct either value.
- A Sandbox MIME artifact is not automatically a native Grafana chart. Never promise downloadable files, native panels, screenshots, or dashboards unless the selected capability returns direct evidence for them.
"""

CAPABILITY_CONFIG = ROOT / "config" / "adaptive-mcp-capabilities.json"


def load_server_specs() -> list[dict[str, Any]]:
    try:
        raw = json.loads(CAPABILITY_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load adaptive MCP capability config: {exc}") from exc
    servers = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, list) or len(servers) != 4 or any(not isinstance(item, dict) for item in servers):
        raise SystemExit("adaptive MCP capability config must define exactly four trust-seam servers")
    required = {"id", "name", "url_env", "local_url", "enabled_tools", "disabled_tools"}
    for server in servers:
        if set(server) != required or not isinstance(server["enabled_tools"], list) or not isinstance(server["disabled_tools"], list):
            raise SystemExit(f"invalid adaptive MCP capability entry: {server.get('id')}")
    ids = [str(item["id"]) for item in servers]
    if ids != ["data-query-planner", "grafana-query", "sandbox-analysis", "grafana-renderer"]:
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
            **({"riskOverrides": {
                "prepare_dashboard_write": {"requiresApproval": False, "readOnly": True, "reason": "Prepares a server-side capability without writing Grafana."},
                "create_temporary_dashboard_preview": {"requiresApproval": True, "readOnly": False, "reason": "Creates an expiring Grafana Preview dashboard."},
                "create_dashboard_from_artifacts": {"requiresApproval": True, "readOnly": False, "reason": "Promotes a confirmed Grafana Preview to a formal dashboard."},
            }} if spec["id"] == "grafana-renderer" else {}),
        }
        for spec in SERVER_SPECS
    ]


def build_json_data(existing: dict[str, Any], use_local_defaults: bool) -> dict[str, Any]:
    json_data = dict(existing)
    json_data.update(
        {
            "mcpServers": build_servers(use_local_defaults),
            "trustedMCPServers": {str(spec["id"]): True for spec in SERVER_SPECS},
            "useBuiltInMCP": True,
            "builtInMCPToolSelections": dict(json_data.get("builtInMCPToolSelections") or {}),
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
    if not isinstance(built_in_mcp, bool) or not built_in_mcp:
        raise SystemExit("useBuiltInMCP must be true so Ask O11y retains native dynamic Grafana query/dashboard capabilities")
    if not isinstance(json_data.get("builtInMCPToolSelections"), dict):
        raise SystemExit("builtInMCPToolSelections must be an object")
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
    for required in ["Analysis Preview", "Result Preview", "Grafana Preview", "formal publication", "explicit confirmation", "There is no fixed workflow", "opaque artifact refs", "approval-gated Grafana write", "dashboard_uid"]:
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
    for retired_id in ("engineering-analysis", "finance-analysis"):
        prefix = f"mcpServerHeader.{retired_id}."
        secure[prefix + "Authorization"] = ""
        secure[prefix + "X-Grafana-Org-Id"] = ""
        secure[prefix + "X-Grafana-User"] = ""
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
        print(json.dumps({"ok": True, "settings_payload": str(args.out.relative_to(ROOT)), "servers": [spec["id"] for spec in SERVER_SPECS], "built_in_mcp": True}, ensure_ascii=False, indent=2))
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
