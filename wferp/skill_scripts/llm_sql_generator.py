import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
from urllib import error, request


JsonDict = dict[str, Any]


def _strip_markdown_fence(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("```"):
        return value
    lines = value.splitlines()
    if len(lines) < 3:
        return value
    if lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return value


def build_llm_prompt(user_prompt: str, context_slice: JsonDict) -> str:
    return (
        "You are a SQL generation engine for Workflow ERP. "
        "Output JSON only with keys: sql, used_tables, assumptions, confidence. "
        "Rules: SQL Server 2000 compatible, single statement, SELECT only, no CTE/window/offset/fetch/except/intersect. "
        f"User prompt: {user_prompt}\n"
        f"Context: {json.dumps(context_slice, ensure_ascii=False)}"
    )


def parse_llm_response(raw_text: str) -> JsonDict:
    payload = json.loads(_strip_markdown_fence(raw_text))
    sql = str(payload.get("sql", "")).strip()
    used_tables = [str(x).strip() for x in payload.get("used_tables", []) if str(x).strip()]
    assumptions = [str(x).strip() for x in payload.get("assumptions", []) if str(x).strip()]

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    if confidence < 0.0:
        confidence = 0.0
    if confidence > 1.0:
        confidence = 1.0

    return {
        "sql": sql,
        "used_tables": used_tables,
        "assumptions": assumptions,
        "confidence": confidence,
    }


def _call_openai_compatible(model: str, prompt_text: str, timeout_sec: float) -> str:
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("LLM_PROVIDER_NOT_CONFIGURED")

    endpoint = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0,
    }
    req = request.Request(
        endpoint,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return str(payload["choices"][0]["message"]["content"])
    except error.HTTPError as exc:
        raise RuntimeError(f"LLM_HTTP_ERROR:{exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError("LLM_NETWORK_ERROR") from exc
    except TimeoutError as exc:
        raise RuntimeError("LLM_TIMEOUT") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("LLM_BAD_RESPONSE") from exc


def _sdk_bridge_path() -> Path:
    return Path(__file__).with_name("llm_sdk") / "bridge.mjs"


def _sdk_error(stderr: str) -> str:
    for line in reversed(str(stderr or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = str(payload.get("code", "")).strip()
        if code:
            return code
    return "LLM_PROVIDER_ERROR"


def _call_sdk(provider: str, model: str, prompt_text: str, timeout_sec: float) -> str:
    node = os.getenv("WFERP_NODE_EXECUTABLE", "").strip() or shutil.which("node")
    if not node:
        raise RuntimeError("NODE_NOT_INSTALLED")

    bridge = _sdk_bridge_path()
    if not bridge.exists():
        raise RuntimeError("LLM_SDK_NOT_INSTALLED")

    payload = {
        "provider": provider,
        "model": str(model or ""),
        "prompt": prompt_text,
        "timeoutSec": float(timeout_sec),
        "cwd": str(Path.cwd()),
    }
    env = os.environ.copy()
    env["WFERP_LLM_PROVIDER"] = provider
    try:
        result = subprocess.run(
            [node, str(bridge)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=max(1.0, float(timeout_sec)) + 5.0,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("NODE_NOT_INSTALLED") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("LLM_TIMEOUT") from exc

    if result.returncode != 0:
        raise RuntimeError(_sdk_error(result.stderr))
    try:
        response = json.loads(str(result.stdout or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM_BAD_RESPONSE") from exc

    text = str(response.get("text", "")).strip()
    if not text:
        raise RuntimeError("LLM_BAD_RESPONSE")
    return text


def call_llm(provider: str, model: str, prompt_text: str, timeout_sec: float = 30.0) -> str:
    provider_name = str(provider or "none").strip().lower()

    if provider_name in {"none", "", "disabled"}:
        raise RuntimeError("LLM_PROVIDER_NOT_CONFIGURED")

    if provider_name == "mock":
        return os.getenv(
            "LLM_MOCK_RESPONSE",
            json.dumps(
                {
                    "sql": "SELECT TOP 50 * FROM [DSCSYS].[dbo].[ADMMC]",
                    "used_tables": ["ADMMC"],
                    "assumptions": ["mock provider fallback"],
                    "confidence": 0.4,
                },
                ensure_ascii=False,
            ),
        )

    if provider_name in {"openai-compatible", "openai_compatible", "openai"}:
        return _call_openai_compatible(model=model, prompt_text=prompt_text, timeout_sec=timeout_sec)

    if provider_name in {"pi", "local-pi"}:
        return _call_sdk(provider="pi", model=model, prompt_text=prompt_text, timeout_sec=timeout_sec)

    if provider_name in {"codex", "openai-codex"}:
        return _call_sdk(provider="codex", model=model, prompt_text=prompt_text, timeout_sec=timeout_sec)

    if provider_name in {"opencode", "open-code", "local-opencode", "native"}:
        return _call_sdk(provider="opencode", model=model, prompt_text=prompt_text, timeout_sec=timeout_sec)

    raise RuntimeError("LLM_PROVIDER_UNSUPPORTED")
