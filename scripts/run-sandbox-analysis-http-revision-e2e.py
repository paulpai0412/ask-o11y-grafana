#!/usr/bin/env python3
"""Authenticated HTTP execute/list/inspect/revise and request-bound E2E."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artifact_store import ArtifactStore  # noqa: E402
OUT = ROOT / ".scratch" / "poc" / "sandbox-analysis-http-e2e.json"
URL = os.environ.get("SANDBOX_ANALYSIS_MCP_URL", "http://127.0.0.1:8777/mcp")
TOKEN = os.environ.get("MCP_SHARED_TOKEN", "")
ORG = os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1")
USER = os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y")


def rpc(name: str, arguments: dict[str, Any], timeout: int = 240) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}", "X-Grafana-Org-Id": ORG, "X-Grafana-User": USER},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.load(response)
        return json.loads(envelope["result"]["content"][0]["text"])
    except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Sandbox Analysis RPC returned an invalid response") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    require(len(TOKEN) >= 32, "MCP_SHARED_TOKEN is required")
    context = {"org_id": ORG, "user_id": USER}
    store = ArtifactStore(ROOT / ".analysis-artifacts" / "runs")
    run_id = store.create_run(context)
    secret_value = "DO_NOT_EXPOSE_RAW_FRAME_VALUE"
    frame_ref = store.write_json(context, run_id, "grafana-frame", [{
        "schema": {"fields": [{"name": "x", "type": "number"}, {"name": "y", "type": "number"}, {"name": "secret", "type": "string"}]},
        "data": {"values": [[1, 2, 3], [2, 4, 6], [secret_value, "safe", "safe"]]},
    }])
    store.write_json(context, run_id, "query-plan", {"analysis_input_contract": {"validity_rules": []}})

    initial_code = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot(df['x'], df['y'])\nax.set_title('熱耗率相關性')\nemit(fig, name='熱耗率趨勢.png')\nemit(df[['x', 'y']], name='分析結果.csv')\nemit({'rows': len(df), 'slope': 2}, name='result.json')\nemit('斜率為 2', name='summary.txt')"
    executed = rpc("execute_python_analysis", {"frame_ref": frame_ref, "python_code": initial_code, "seed": 41})
    require(bool(executed.get("ok")), f"execute failed: {executed}")
    require(executed.get("output_summary", {}).get("mime_types") == ["application/json", "image/png", "text/csv", "text/plain"], "named result MIME capture failed")
    require(executed.get("output_summary", {}).get("output_names") == ["熱耗率趨勢.png", "分析結果.csv", "result.json", "summary.txt"], "output display names were not preserved")
    require(executed.get("output_summary", {}).get("inline_results") == [
        {"output_index": 2, "display_name": "result.json", "mime_type": "application/json", "value": {"rows": 3, "slope": 2}},
        {"output_index": 3, "display_name": "summary.txt", "mime_type": "text/plain", "value": "斜率為 2"},
    ], "bounded inline results were not returned")
    downloads = executed.get("output_summary", {}).get("downloads", [])
    require(len(downloads) == 1 and downloads[0].get("display_name") == "分析結果.csv", "signed CSV download was not returned")
    with urllib.request.urlopen(downloads[0]["url"], timeout=30) as response:
        require(response.read() == b"x,y\n1,2\n2,4\n3,6\n", "signed CSV download content mismatch")
        require(response.headers.get("Content-Disposition", "").startswith("attachment;"), "CSV response is not a download")
    require(executed.get("output_summary", {}).get("stderr_lines") == 0, "CJK plot emitted font warnings")
    provenance_ref = executed["refs"]["provenance_ref"]
    listed = rpc("list_python_analyses", {})
    require(any(item.get("provenance_ref") == provenance_ref for item in listed.get("analyses", [])), "new analysis was not listed")
    inspected = rpc("inspect_python_analysis", {"provenance_ref": provenance_ref})
    require(inspected.get("python_code") == initial_code, "inspect did not recover source")
    revised = rpc("revise_python_analysis", {"provenance_ref": provenance_ref, "python_code": "display(df[['x', 'y']].head(2))", "seed": 42})
    require(bool(revised.get("ok")) and revised.get("provenance", {}).get("parent_provenance_ref") == provenance_ref, f"revision lineage failed: {revised}")

    redacted = rpc("execute_python_analysis", {"frame_ref": frame_ref, "python_code": "raise type(str(df.iloc[0]['secret']), (Exception,), {})()", "seed": 43})
    require(not redacted.get("ok") and secret_value not in json.dumps(redacted), "exception name leaked a raw frame value")
    flooded = rpc("execute_python_analysis", {"frame_ref": frame_ref, "python_code": "import os\nos.write(1, b'x' * (1024 * 1024))", "seed": 44})
    require(not flooded.get("ok") and len(json.dumps(flooded)) < 4096, "sandbox log flood escaped the streaming bound")

    oversized_status = None
    oversized_request = urllib.request.Request(
        URL,
        data=b"{" + b"x" * (128 * 1024 + 1),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}", "X-Grafana-Org-Id": ORG, "X-Grafana-User": USER},
    )
    try:
        urllib.request.urlopen(oversized_request, timeout=30)
    except urllib.error.HTTPError as exc:
        oversized_status = exc.code
    require(oversized_status == 413, f"oversized HTTP body returned {oversized_status}")

    evidence = {
        "ok": True,
        "execute_mime_types": executed["output_summary"]["mime_types"],
        "named_outputs": executed["output_summary"]["output_names"],
        "inline_results": executed["output_summary"]["inline_results"],
        "csv_download": True,
        "cjk_font_warnings": executed["output_summary"]["stderr_lines"],
        "listed": True,
        "inspect_code_sha256": inspected["code_sha256"],
        "revise_ok": True,
        "parent_linked": True,
        "exception_name_and_value_redacted": True,
        "sandbox_log_flood_bounded": True,
        "oversized_http_status": oversized_status,
        "raw_frame_in_model_result": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
