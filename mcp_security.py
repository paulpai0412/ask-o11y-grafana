"""Authenticated local HTTP boundary shared by the adaptive MCP servers."""
from __future__ import annotations

import hmac
import os
from typing import Any


def runtime_bind_host() -> str:
    return os.environ.get("MCP_BIND_HOST", "127.0.0.1")


def require_runtime_token() -> str:
    token = os.environ.get("MCP_SHARED_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("MCP_SHARED_TOKEN must contain at least 32 characters")
    return token


def require_service_identity() -> dict[str, str]:
    org = os.environ.get("ANALYSIS_SERVICE_ORG_ID", "")
    user = os.environ.get("ANALYSIS_SERVICE_USER_ID", "")
    if not org or not user:
        raise RuntimeError("ANALYSIS_SERVICE_ORG_ID and ANALYSIS_SERVICE_USER_ID are required")
    return {"org_id": org, "user_id": user}


def authenticate_headers(headers: Any) -> dict[str, str] | None:
    try:
        token = require_runtime_token()
        expected = require_service_identity()
    except RuntimeError:
        return None
    supplied = str(headers.get("Authorization") or "")
    if not hmac.compare_digest(supplied, f"Bearer {token}"):
        return None
    org = headers.get("X-Grafana-Org-Id") or headers.get("X-Org-Id")
    user = headers.get("X-Grafana-User-Id") or headers.get("X-Grafana-User") or headers.get("X-Forwarded-User") or headers.get("X-User-Id")
    if not org or not user or not hmac.compare_digest(str(org), expected["org_id"]) or not hmac.compare_digest(str(user), expected["user_id"]):
        return None
    return expected
