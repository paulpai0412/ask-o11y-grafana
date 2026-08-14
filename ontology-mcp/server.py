#!/usr/bin/env python3
"""Bounded read-only Ontology MCP for Ask O11y."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contract = load_module("ontology_contract", ROOT / "ontology_contract.py")
ontology_graph = load_module("ontology_graph", ROOT / "ontology_graph.py")
mcp_security = load_module("mcp_security", ROOT / "mcp_security.py")
authenticate_headers = mcp_security.authenticate_headers
require_runtime_token = mcp_security.require_runtime_token
require_service_identity = mcp_security.require_service_identity
runtime_bind_host = mcp_security.runtime_bind_host

try:
    PORT = int(os.environ.get("ONTOLOGY_MCP_PORT", "8771"))
except ValueError:
    PORT = 8771
SERVER_INFO = {"name": "ontology-mcp", "version": "0.2.0"}
PROTOCOL = "2025-03-26"
MAX_TERMS = 16
MAX_DATASETS = 50
MAX_FIELDS = 200
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RELATIONS = 50


def response(step: str, **values: Any) -> dict[str, Any]:
    output = {"ok": True, "step": step, **values}
    if len(json.dumps(output, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
        raise ValueError("ontology response exceeds bounded output limit")
    return output


def load_verified(snapshot_ref: str | None = None, dataset_id: str | None = None, namespace: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    snapshot = contract.load_snapshot(snapshot_ref=snapshot_ref, dataset_id=dataset_id, namespace=namespace)
    mismatch = contract.verify_snapshot_ref(snapshot, snapshot_ref)
    if mismatch:
        raise ValueError(mismatch)
    return snapshot, contract.snapshot_identity(snapshot)


def tool_list_snapshots(args: dict[str, Any]) -> dict[str, Any]:
    entries = contract.list_snapshots(args.get("namespace"), args.get("dataset_id"))
    manifests = []
    for entry in entries[:64]:
        dataset_ids = entry["dataset_ids"]
        manifests.append({"snapshot_id": entry["snapshot_id"], "namespace": entry["namespace"], "dataset_ids": dataset_ids[:MAX_DATASETS], "dataset_count": len(dataset_ids), "dataset_ids_truncated": len(dataset_ids) > MAX_DATASETS, "sha256": entry["sha256"], "status": entry["status"]})
    return response("list_snapshots", snapshots=manifests)


def tool_get_relation_paths(args: dict[str, Any]) -> dict[str, Any]:
    seeds = args.get("dataset_ids")
    if not isinstance(seeds, list) or not seeds or len(seeds) > MAX_DATASETS or any(not isinstance(seed, str) or not seed for seed in seeds):
        raise ValueError("dataset_ids must be a non-empty bounded array")
    snapshot, identity = load_verified(args.get("snapshot_ref"), dataset_id=seeds[0])
    try:
        max_hops, limit = int(args.get("max_hops", 2)), int(args.get("limit", 16))
    except (TypeError, ValueError) as exc:
        raise ValueError("max_hops and limit must be integers") from exc
    expanded = ontology_graph.expand_datasets(snapshot, seeds, max_hops, limit, bool(args.get("include_proposed", False)))
    return response("get_relation_paths", snapshot=identity, expansion=expanded)


def tool_resolve_concepts(args: dict[str, Any]) -> dict[str, Any]:
    terms = args.get("terms")
    if not isinstance(terms, list) or not terms or len(terms) > MAX_TERMS or any(not isinstance(term, str) or not term.strip() or len(term) > 128 for term in terms):
        raise ValueError(f"terms must contain 1..{MAX_TERMS} bounded strings")
    snapshot, identity = load_verified(args.get("snapshot_ref"), namespace=args.get("namespace"))
    candidates = []
    for term in terms:
        needle = term.casefold()
        matches = []
        for dataset in snapshot["registry"]["datasets"]:
            if needle in {str(dataset["physical_id"]).casefold(), str(dataset["canonical_id"]).casefold()}:
                matches.append({"canonical_id": dataset["canonical_id"], "physical_id": dataset["physical_id"], "kind": "dataset", "status": dataset["status"]})
            for field in dataset["fields"]:
                haystack = {str(field["physical_name"]).casefold(), str(field["canonical_id"]).casefold()}
                if needle in haystack:
                    matches.append({"canonical_id": field["canonical_id"], "physical_name": field["physical_name"], "kind": "field", "status": field["status"], "role": field.get("analysis_role"), "reason": field["reason"]})
        candidates.append({"term": term, "matches": matches[:16], "ambiguous": len(matches) > 1})
    return response("resolve_concepts", snapshot=identity, results=candidates)


def tool_get_semantic_context(args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = args.get("dataset_id")
    intent = args.get("intent")
    fields = args.get("fields", [])
    if not isinstance(dataset_id, str) or not dataset_id or not isinstance(intent, str) or not intent or len(intent) > 2048:
        raise ValueError("dataset_id and bounded intent are required")
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS or any(not isinstance(field, str) or not field for field in fields):
        raise ValueError(f"fields must be a bounded array of at most {MAX_FIELDS} names")
    snapshot, identity = load_verified(args.get("snapshot_ref"), dataset_id=dataset_id)
    dataset = contract.find_dataset(snapshot, dataset_id)
    if dataset is None:
        raise ValueError("UNKNOWN_DATASET")
    by_name = contract.fields_by_name(dataset)
    requested = fields or [field["physical_name"] for field in dataset["fields"]]
    unknown = [field for field in requested if field not in by_name]
    if unknown:
        raise ValueError("UNKNOWN_FIELD: " + ", ".join(unknown))
    selected = [contract.field_view(by_name[field]) for field in requested]
    return response(
        "get_semantic_context",
        snapshot=identity,
        context={
            "dataset_id": dataset["physical_id"],
            "canonical_id": dataset["canonical_id"],
            "intent": intent,
            "namespace": identity["namespace"],
            "asset_kind": dataset.get("asset_kind", "tabular_file"),
            "status": dataset["status"],
            "grain": dataset["grain"],
            "entity_key": dataset["entity_key"],
            "time_identity": dataset.get("time_identity"),
            "target": dataset.get("target"),
            "approved_features": dataset.get("approved_features", []),
            "quality_policy": dataset.get("quality_policy"),
            "split_policy": dataset.get("split_policy"),
            "relations": dataset.get("relations", [])[:MAX_RELATIONS],
            "relations_truncated": len(dataset.get("relations", [])) > MAX_RELATIONS,
            "fields": selected,
        },
    )


def tool_classify_fields(args: dict[str, Any]) -> dict[str, Any]:
    dataset_id, fields = args.get("dataset_id"), args.get("fields")
    if not isinstance(dataset_id, str) or not isinstance(fields, list) or not fields or len(fields) > MAX_FIELDS or any(not isinstance(field, str) or not field for field in fields):
        raise ValueError(f"dataset_id and 1..{MAX_FIELDS} fields are required")
    snapshot, identity = load_verified(args.get("snapshot_ref"), dataset_id=dataset_id)
    dataset = contract.find_dataset(snapshot, dataset_id)
    if dataset is None:
        raise ValueError("UNKNOWN_DATASET")
    by_name = contract.fields_by_name(dataset)
    classifications = []
    for name in fields:
        field = by_name.get(name)
        classifications.append({"requested": name, "found": field is not None, "classification": contract.field_view(field) if field else None})
    return response("classify_fields", snapshot=identity, classifications=classifications)


def tool_validate_analysis_contract(args: dict[str, Any]) -> dict[str, Any]:
    analysis_contract = args.get("contract")
    if not isinstance(analysis_contract, dict):
        raise ValueError("contract must be an object")
    snapshot, _identity = load_verified(args.get("snapshot_ref"), dataset_id=analysis_contract.get("dataset_id"))
    return response("validate_analysis_contract", validation=contract.validate_analysis_contract(snapshot, analysis_contract, args.get("snapshot_ref")))


TOOLS = [
    {"name": "list_snapshots", "description": "List at most 64 approved immutable snapshot manifests, optionally filtered by namespace or dataset. Never returns registry contents or rows.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"namespace": {"type": "string", "minLength": 1, "maxLength": 128}, "dataset_id": {"type": "string", "minLength": 1, "maxLength": 128}}}},
    {"name": "get_relation_paths", "description": "Expand bounded dataset seeds through approved executable ontology relations, returning exact keys/cardinality/path evidence. Proposed relations are excluded by default and never become executable.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_ids": {"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 128}}, "max_hops": {"type": "integer", "minimum": 0, "maximum": 3, "default": 2}, "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 16}, "include_proposed": {"type": "boolean", "default": False}, "snapshot_ref": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["dataset_ids"]}},
    {"name": "resolve_concepts", "description": "Resolve at most 16 exact dataset/field terms against one approved immutable semantic snapshot. Returns ambiguity; never queries data.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"terms": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": "string", "minLength": 1, "maxLength": 128}}, "namespace": {"type": "string", "minLength": 1, "maxLength": 128}, "snapshot_ref": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["terms"]}},
    {"name": "get_semantic_context", "description": "Return bounded grain, identity, roles, availability, quality, feature allowlist, and split policy for one approved dataset snapshot; never returns rows or a graph dump.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_id": {"type": "string", "minLength": 1, "maxLength": 128}, "intent": {"type": "string", "minLength": 1, "maxLength": 2048}, "fields": {"type": "array", "maxItems": 200, "items": {"type": "string", "minLength": 1, "maxLength": 128}}, "snapshot_ref": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["dataset_id", "intent"]}},
    {"name": "classify_fields", "description": "Classify at most 200 exact fields by semantic kind, analysis role, approval, availability, unit, lineage, and evidence.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"dataset_id": {"type": "string", "minLength": 1, "maxLength": 128}, "fields": {"type": "array", "minItems": 1, "maxItems": 200, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 128}}, "snapshot_ref": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["dataset_id", "fields"]}},
    {"name": "validate_analysis_contract", "description": "Side-effect-free advisory semantic validation. Planner independently enforces the same snapshot before making a query plan executable.", "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"contract": {"type": "object", "maxProperties": 16}, "snapshot_ref": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["contract"]}},
]
HANDLERS = {"list_snapshots": tool_list_snapshots, "get_relation_paths": tool_get_relation_paths, "resolve_concepts": tool_resolve_concepts, "get_semantic_context": tool_get_semantic_context, "classify_fields": tool_classify_fields, "validate_analysis_contract": tool_validate_analysis_contract}


def rpc_result(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_rpc(msg: dict[str, Any]):
    method, rid = msg.get("method", ""), msg.get("id")
    if method == "initialize":
        return rpc_result(rid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return rpc_result(rid, {})
    if method == "tools/list":
        return rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        name, args = params.get("name", ""), params.get("arguments", {}) or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        if not isinstance(args, dict):
            return rpc_error(rid, -32602, "tool arguments must be an object")
        schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
        unexpected = sorted(set(args) - set(schema["properties"]))
        try:
            output = handler(args) if not unexpected else (_ for _ in ()).throw(ValueError("unsupported tool arguments: " + ", ".join(unexpected)))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            output = {"ok": False, "step": name, "error": str(exc), "rejection_codes": [str(exc).split(":", 1)[0]]}
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}], "isError": not output.get("ok", False)})
    if rid is None:
        return None
    return rpc_error(rid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, obj: Any = None) -> None:
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self._send(405 if self.path.rstrip("/") == "/mcp" else 404, {"error": "POST JSON-RPC to /mcp"})

    def do_DELETE(self):
        self._send(405, {"error": "read-only service"})

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            return self._send(404, {"error": "not found"})
        if authenticate_headers(self.headers) is None:
            return self._send(401, {"error": "authenticated MCP service identity is required"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, TypeError):
            return self._send(400, rpc_error(None, -32700, "parse error"))
        messages = payload if isinstance(payload, list) else [payload]
        replies = [reply for message in messages if isinstance(message, dict) and (reply := handle_rpc(message)) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):  # noqa: A002
        sys.stderr.write("ontology-mcp " + format % args + "\n")


def self_check() -> None:
    snapshot = contract.load_snapshot()
    identity = contract.snapshot_identity(snapshot)
    context = tool_get_semantic_context({"dataset_id": "u1-operating-daily", "intent": "retrospective heat_rate SHAP", "snapshot_ref": identity["sha256"]})
    if context["context"]["approved_features"] == [] or context["snapshot"] != identity:
        raise RuntimeError("bounded context self-check failed")
    classification = tool_classify_fields({"dataset_id": "u1-operating-daily", "fields": ["heat_rate", "raw_coal_consumption_g"]})
    roles = {item["requested"]: item["classification"]["analysis_role"] for item in classification["classifications"]}
    if roles != {"heat_rate": "target", "raw_coal_consumption_g": "forbidden"}:
        raise RuntimeError(f"unsafe classification: {roles}")
    wide_fields = [f"field_{index}" for index in range(MAX_FIELDS)]
    if len(tool_classify_fields({"dataset_id": "u1-operating-daily", "fields": wide_fields})["classifications"]) != MAX_FIELDS:
        raise RuntimeError("200-field classification boundary failed")
    try:
        tool_classify_fields({"dataset_id": "u1-operating-daily", "fields": [*wide_fields, "field_200"]})
    except ValueError:
        pass
    else:
        raise RuntimeError("201-field classification was allowed")
    wferp = tool_get_semantic_context({"dataset_id": "ACPTA", "intent": "inspect voucher relationships"})
    relation = wferp["context"]["relations"][0]
    observability = tool_get_semantic_context({"dataset_id": "http-server-request", "intent": "inspect HTTP latency", "fields": ["http.server.request.duration"]})
    relation_expansion = tool_get_relation_paths({"dataset_ids": ["ACTMK"], "max_hops": 2, "limit": 8})
    manifests = tool_list_snapshots({})["snapshots"]
    catalog_entries = contract.load_catalog()["snapshots"]
    expanded = relation_expansion["expansion"]
    if len(manifests) != len(catalog_entries) or any(not item["snapshot_id"] or not item["sha256"] for item in manifests) or observability["context"]["asset_kind"] != "event_topic" or not {"ACTMI", "ACTMJ", "ACTMK"}.issubset(set(expanded["datasets"])) or any(path["relation"]["status"] != "approved" for path in expanded["paths"]):
        raise RuntimeError("generic catalog fixture self-check failed")
    forbidden = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_semantic_context", "arguments": {"dataset_id": "u1-operating-daily", "intent": "x", "sql": "SELECT *"}}})
    listed = handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    if forbidden is None or listed is None:
        raise RuntimeError("MCP self-check returned no response")
    tool_names = [tool["name"] for tool in listed["result"]["tools"]]
    if not forbidden["result"]["isError"] or any(name in tool_names for name in ("mutate", "query", "dump_graph")):
        raise RuntimeError("read-only tool contract self-check failed")
    print(json.dumps({"ok": True, "snapshot": identity, "catalog_snapshots": [item["snapshot_id"] for item in manifests], "fixtures": {"tabular_file": "u1-operating-daily", "event_topic": "http-server-request"}, "relation_expansion": {"seed": "ACTMK", "datasets": expanded["datasets"], "path_count": len(expanded["paths"])}, "tools": tool_names, "negative_checks": ["raw_sql", "no_mutation", "no_graph_dump", "proxy_forbidden"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    require_runtime_token()
    require_service_identity()
    host = runtime_bind_host()
    print(f"{SERVER_INFO['name']} {SERVER_INFO['version']} on {host}:{PORT}", file=sys.stderr)
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
