#!/usr/bin/env python3
"""Replay one owned plan through the live Grafana Query MCP for diagnosis."""
from __future__ import annotations

import argparse
import json
import os
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_ref")
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--url", default=os.environ.get("GRAFANA_QUERY_MCP_URL", "http://127.0.0.1:8772/mcp"))
    args = parser.parse_args()
    token = os.environ.get("MCP_SHARED_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("MCP_SHARED_TOKEN is required")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", "X-Grafana-Org-Id": os.environ.get("ANALYSIS_SERVICE_ORG_ID", "1"), "X-Grafana-User": os.environ.get("ANALYSIS_SERVICE_USER_ID", "ask-o11y"), "X-Grafana-Actor-User-Id": args.actor_user_id}
    if args.session_id:
        headers["X-Grafana-Session-Id"] = args.session_id
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "execute_planned_query", "arguments": {"plan_ref": args.plan_ref}}}
    request = urllib.request.Request(args.url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            output = json.loads(response.read())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"plan replay failed: {exc}") from exc
    print(json.dumps(output, ensure_ascii=False, indent=2))
    result = output.get("result", {})
    return 1 if result.get("isError") else 0


if __name__ == "__main__":
    raise SystemExit(main())
