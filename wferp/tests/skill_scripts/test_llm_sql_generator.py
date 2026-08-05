import json
from types import SimpleNamespace

import pytest

from skill_scripts.llm_sql_generator import build_llm_prompt, call_llm, parse_llm_response


def test_parse_llm_response_extracts_sql_and_tables():
    payload = '{"sql":"SELECT TOP 10 * FROM [VPIC1].[dbo].[ACTMK]","used_tables":["ACTMK"],"assumptions":["year filter"],"confidence":0.82}'
    out = parse_llm_response(payload)
    assert out["sql"].startswith("SELECT")
    assert out["used_tables"] == ["ACTMK"]
    assert out["assumptions"] == ["year filter"]
    assert 0.0 <= out["confidence"] <= 1.0


def test_build_llm_prompt_contains_user_prompt_and_context():
    prompt = build_llm_prompt("查詢預算明細", {"tables": [{"TableID": "ACTMK"}]})
    assert "查詢預算明細" in prompt
    assert "ACTMK" in prompt


def test_call_llm_mock_provider(monkeypatch):
    mock = {
        "sql": "SELECT TOP 5 * FROM [VPIC1].[dbo].[ACTMK]",
        "used_tables": ["ACTMK"],
        "assumptions": [],
        "confidence": 0.9,
    }
    monkeypatch.setenv("LLM_MOCK_RESPONSE", json.dumps(mock, ensure_ascii=False))
    raw = call_llm(provider="mock", model="unused", prompt_text="anything", timeout_sec=1.0)
    parsed = parse_llm_response(raw)
    assert parsed["sql"].startswith("SELECT TOP 5")


def test_call_llm_none_provider_raises():
    try:
        call_llm(provider="none", model="unused", prompt_text="x")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "LLM_PROVIDER_NOT_CONFIGURED"


def test_parse_llm_response_accepts_markdown_fence():
    fenced = """```json
{"sql":"SELECT TOP 1 * FROM [VPIC1].[dbo].[ACTMK]","used_tables":["ACTMK"],"assumptions":[],"confidence":0.8}
```"""
    parsed = parse_llm_response(fenced)
    assert parsed["sql"].startswith("SELECT TOP 1")
    assert parsed["used_tables"] == ["ACTMK"]


@pytest.mark.parametrize("provider", ["pi", "codex", "opencode"])
def test_call_llm_sdk_providers_use_node_bridge(monkeypatch, provider):
    response = {
        "text": '{"sql":"SELECT TOP 3 * FROM [VPIC1].[dbo].[ACTMK]","used_tables":["ACTMK"],"assumptions":[],"confidence":0.77}'
    }

    def _fake_run(command, input, capture_output, text, encoding, check, timeout, env):
        assert command[1].endswith("skill_scripts/llm_sdk/bridge.mjs")
        payload = json.loads(input)
        assert payload["provider"] == provider
        assert payload["prompt"] == "anything"
        assert timeout == 6.0
        assert env["WFERP_LLM_PROVIDER"] == provider
        return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")

    monkeypatch.setattr("skill_scripts.llm_sql_generator.subprocess.run", _fake_run)
    monkeypatch.setattr("skill_scripts.llm_sql_generator.shutil.which", lambda name: "/usr/bin/node")

    raw = call_llm(provider=provider, model="none", prompt_text="anything", timeout_sec=1.0)
    parsed = parse_llm_response(raw)
    assert parsed["sql"].startswith("SELECT TOP 3")
