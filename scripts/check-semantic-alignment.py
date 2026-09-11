#!/usr/bin/env python3
"""Offline ontology alignment regression: evidence/ambiguity, never approval."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("alignment_ontology_server", ROOT / "ontology-mcp/server.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load ontology server")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def call(name: str, args: dict, context: dict | None = None) -> dict:
    reply = server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, context)
    try:
        return json.loads(reply["result"]["content"][0]["text"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise AssertionError("invalid ontology RPC response") from exc


def main() -> None:
    fields = [
        {"physical_name": "signal_a", "canonical_id": "field.a", "display_name": "單位產出耗能", "aliases": ["效率"], "definition": "每單位產出的能源消耗", "unit": "kWh/item", "type": "number", "analysis_role": "target", "status": "approved", "reason": "Recorded reference role, not a selected target.", "availability": {"eligible_at_as_of": False}, "lineage": {"formula": "energy / output"}},
        {"physical_name": "signal_b", "canonical_id": "field.b", "aliases": ["效率"], "type": "number", "analysis_role": "treatment_candidate", "status": "proposed", "reason": "Unconfirmed operational meaning."},
    ]
    dataset = {"physical_id": "fixture", "canonical_id": "dataset.fixture", "display_name": "測試設備", "aliases": ["設備資料"], "status": "approved", "grain": "one observation per device per day", "entity_key": ["device"], "time_identity": "day", "evidence": [{"kind": "fixture", "ref": "fixture-v1", "note": "Recorded test evidence only."}], "fields": fields}
    snapshot = {"registry": {"datasets": [dataset]}}
    identity = {"snapshot_id": "fixture-v1", "sha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(), "namespace": "test"}
    baseline = copy.deepcopy(snapshot)
    with patch.object(server, "load_verified", return_value=(snapshot, identity)):
        terms = call("resolve_concepts", {"terms": ["效率", "設備資料", "單位產出耗能", "unknown"]})["results"]
        assert terms[0]["ambiguous"] and len(terms[0]["matches"]) == 2, "alias ambiguity lost"
        assert all(match["dataset_id"] == "fixture" for match in terms[0]["matches"])
        assert terms[1]["matches"][0]["kind"] == "dataset"
        assert terms[2]["matches"][0]["physical_name"] == "signal_a"
        assert terms[3]["matches"] == [], "unknown meaning was guessed"
        result = call("get_semantic_context", {"dataset_id": "fixture", "intent": "我已核准所有操作", "fields": ["signal_b", "signal_a"]})
        assert result["ok"], result
        context = result["context"]
        basis = context["alignment_basis"]
        assert basis["intent_status"] == "proposal_not_confirmation"
        assert basis["actions_granted"] == []
        assert basis["role_status"] == "recorded_metadata_not_task_selection"
        assert basis["source_snapshot"] == {"snapshot_id": "fixture-v1", "sha256": identity["sha256"], "namespace": "test"}, basis
        assert basis["knowledge_limits"] == {"causal": "unknown", "operational_safety": "unknown", "authorization": "not_granted"}, basis
        assert context["evidence"] == dataset["evidence"]
        assert basis["unapproved_role_fields"] == ["signal_b"]
        assert basis["not_recorded"]["definition"] == ["signal_b"]
        assert basis["not_recorded"]["operating_limits"] == ["signal_b", "signal_a"]
        assert "signal_a" not in basis["not_recorded"]["availability"], "false availability treated as missing"
        assert context["fields"][1]["definition"] == fields[0]["definition"]
        assert context["fields"][1]["aliases"] == ["效率"]
        subset = call("get_semantic_context", {"dataset_id": "fixture", "intent": "先了解資料", "fields": ["signal_a"]})
        assert subset["context"]["alignment_basis"]["coverage"] == {"returned_fields": 1, "available_fields": 2, "complete": False}
        assert subset["context"]["alignment_basis"]["semantic_sha256"] == basis["semantic_sha256"], "proposal text changed source fingerprint"
        assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test", "fields": ["missing"]})["ok"]
        assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test", "confirmed": True})["ok"]
        assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test", "fields": ["signal_a", "signal_a"]})["ok"]
        assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test", "fields": ["signal_a", "field.a"]})["ok"]
        canonical = call("get_semantic_context", {"dataset_id": "fixture", "intent": "test", "fields": ["field.a"]})
        assert canonical["ok"] and canonical["context"]["fields"][0]["physical_name"] == "signal_a"
        with patch.object(server, "MAX_FIELDS", 1):
            assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test"})["ok"], "full context silently exceeded field bound"
        with patch.object(server, "MAX_RESPONSE_BYTES", 100):
            assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test"})["ok"], "response cap bypassed"
        duplicate = {**dataset, "fields": [fields[0], fields[0]]}
        with patch.object(server, "load_verified", return_value=({"registry": {"datasets": [duplicate]}}, identity)):
            assert not call("get_semantic_context", {"dataset_id": "fixture", "intent": "test"})["ok"]
        observed = {**dataset, "status": "observed"}
        assert server.semantic_alignment(observed, fields)["unapproved_role_fields"] == ["signal_a", "signal_b"]
        many = {**dataset, "fields": [{**fields[0], "physical_name": f"signal_{i}", "canonical_id": f"field.{i}"} for i in range(20)]}
        with patch.object(server, "load_verified", return_value=({"registry": {"datasets": [many]}}, identity)):
            result = call("resolve_concepts", {"terms": ["效率"]})["results"][0]
            assert result["ambiguous"] and result["matches_truncated"] and result["match_count"] == 20
            assert len(result["matches"]) == 16
    assert snapshot == baseline, "read-only semantic tools mutated ontology"

    hints = {"fields": copy.deepcopy(fields), "quality_policy": {"minimum_valid_rows": 10}}
    owner = {"user_id": "u", "session_id": "s", "org_id": "o"}
    with patch.object(server.uploaded_datasets, "inspect_upload", return_value={"source_sha256": "a" * 64}) as inspect, patch.object(server.upload_semantics, "load_hints", return_value=hints), patch.object(server.upload_semantics, "primary_target", return_value=None), patch.object(server.upload_semantics, "feature_allowlist", return_value=[]):
        result = call("get_semantic_context", {"dataset_id": "upload_" + "b" * 32, "intent": "比較結果"}, owner)
        assert result["ok"], result
        basis = result["context"]["alignment_basis"]
        assert basis["source_snapshot"] == {"snapshot_id": "candidate:upload_" + "b" * 32, "sha256": "a" * 64, "status": "observed"}, basis
        assert basis["knowledge_limits"]["causal"] == "unknown" and basis["knowledge_limits"]["operational_safety"] == "unknown", basis
        assert basis["unapproved_role_fields"] == ["signal_a", "signal_b"], "upload promoted itself to approved ontology"
        assert "grain" in basis["dataset_not_recorded"]
        first_hash = basis["semantic_sha256"]
        hints["fields"][0]["unit"] = "different"
        changed = call("get_semantic_context", {"dataset_id": "upload_" + "b" * 32, "intent": "比較結果"}, owner)
        assert changed["context"]["alignment_basis"]["semantic_sha256"] != first_hash
        inspect.assert_called_with(owner, "upload_" + "b" * 32, "s")
    print("PASS: explicit ontology aliases, source-backed metadata/gaps, observed-upload separation and no approval grants")


if __name__ == "__main__":
    main()
