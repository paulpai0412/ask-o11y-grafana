"""Short-lived signed URLs for authorized analysis outputs."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Any

MAX_ASSET_BYTES = 3 * 1024 * 1024
SUPPORTED_MIME = {"image/png", "text/csv", "text/html", "text/plain", "application/json"}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_output_url(*, public_base: str, secret: str, context: dict[str, str], execution_ref: str, output_index: int, expires_at: int) -> str:
    payload = json.dumps({"org": context["org_id"], "user": context["user_id"], "ref": execution_ref, "index": output_index, "exp": expires_at}, separators=(",", ":"), sort_keys=True).encode()
    encoded = _b64(payload)
    signature = _b64(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return public_base.rstrip("/") + "/assets/" + encoded + "." + signature


def read_signed_output(token: str, *, secret: str, artifacts) -> tuple[bytes, str, str]:
    try:
        encoded, signature = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("invalid artifact signature")
        payload = json.loads(_unb64(encoded))
        if int(payload["exp"]) < int(time.time()):
            raise PermissionError("artifact URL expired")
        context = {"org_id": str(payload["org"]), "user_id": str(payload["user"])}
        execution = artifacts.read_json(context, str(payload["ref"]))
        index = int(payload["index"])
        result = execution["results"][index]
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise PermissionError("invalid artifact URL") from exc
    mime = result.get("mime") if isinstance(result, dict) else None
    display_name = str(result.get("display_name") or f"output-{index + 1}") if isinstance(result, dict) else f"output-{index + 1}"
    if isinstance(mime, dict):
        for mime_type in SUPPORTED_MIME:
            value = mime.get(mime_type)
            if isinstance(value, str):
                data = base64.b64decode(value, validate=True) if mime_type == "image/png" else value.encode()
                if len(data) > MAX_ASSET_BYTES:
                    raise PermissionError("artifact exceeds URL output limit")
                return data, mime_type, display_name
    text = result.get("text") if isinstance(result, dict) else None
    if isinstance(text, str) and len(text.encode()) <= MAX_ASSET_BYTES:
        return text.encode(), "text/plain", display_name
    raise PermissionError("artifact output is not URL-compatible")
