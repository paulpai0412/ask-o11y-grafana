#!/usr/bin/env python3
"""wferp-mcp — MCP (streamable-http) server exposing Workflow ERP metadata + validated SQL execution.

Thin adapter over wferp/skill_scripts (single source of truth for schema + guards).
Stdlib only. See README.md.
"""
import importlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn

HERE = Path(__file__).resolve().parent
WFERP_HOME = Path(os.environ.get("WFERP_HOME", HERE.parent / "wferp")).resolve()
sys.path.insert(0, str(WFERP_HOME))


def _fatal(msg: str) -> NoReturn:
    sys.stderr.write(f"wferp-mcp fatal: {msg}\n")
    sys.exit(1)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        _fatal(f"cannot load {path}: {exc}")


try:
    metadata_validator = importlib.import_module("skill_scripts.metadata_validator")
    sql2000_guard = importlib.import_module("skill_scripts.sql2000_guard")
    schema_context_builder = importlib.import_module("skill_scripts.schema_context_builder")
    llm_sql_generator = importlib.import_module("skill_scripts.llm_sql_generator")
    prompt_sql_consistency = importlib.import_module("skill_scripts.prompt_sql_consistency")
except Exception as exc:
    _fatal(f"cannot import skill_scripts from WFERP_HOME={WFERP_HOME}: {exc}")

# defaults for wferp's openai-compatible LLM path (used by generate_sql mode=llm);
# hits pi-gateway directly — NOT grafana-llm-app — so no max_tokens limitation.
os.environ.setdefault("LLM_BASE_URL", os.environ.get("WFERP_MCP_LLM_BASE_URL", "http://localhost:4000/v1"))
os.environ.setdefault("LLM_API_KEY", os.environ.get("WFERP_MCP_LLM_API_KEY", "wferp-mcp"))
DEFAULT_LLM_MODEL = os.environ.get("WFERP_MCP_LLM_MODEL", "gpt-4.1")

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
TOKEN_FILE = os.environ.get("GRAFANA_TOKEN_FILE", str(HERE.parent / ".scratch/t2/sa-token.txt"))
try:
    GRAFANA_TOKEN = os.environ.get("GRAFANA_TOKEN") or Path(TOKEN_FILE).read_text().strip()
except Exception as exc:
    _fatal(f"cannot read Grafana token ({TOKEN_FILE}): {exc}")
DATASOURCE_UID = os.environ.get("MSSQL_DATASOURCE_UID", "afu9h8zppg64gd")  # wferp-test
try:
    PORT = int(os.environ.get("WFERP_MCP_PORT", "8765"))
except ValueError:
    _fatal(f"invalid WFERP_MCP_PORT: {os.environ.get('WFERP_MCP_PORT')!r}")

ART = WFERP_HOME / "skill_scripts" / "artifacts"
BUNDLE = _load_json(ART / "schema_bundle.json")
EDGES = _load_json(ART / "relationship_edges.json")
PK_MAP = _load_json(ART / "primary_key_map.json")

ADJ: dict[str, list[dict]] = {}
for e in EDGES:
    ADJ.setdefault(e["from_table"], []).append(e)
    ADJ.setdefault(e["to_table"], []).append(
        {**e, "from_table": e["to_table"], "to_table": e["from_table"],
         "from_columns": e["to_columns"], "to_columns": e["from_columns"]})


def tool_search_tables(query: str, limit: int = 20):
    q = (query or "").strip().upper()
    if not q:
        return {"error": "query is required"}
    scored = []
    for t in BUNDLE["tables"]:
        tid = t["TableID"].upper()
        hay = f"{t['TableID']} {t.get('TableName','')} {t.get('ModuleName','')} {t.get('TableNameViet','')}".upper()
        if q == tid:
            s = 100
        elif tid.startswith(q):
            s = 60
        elif q in hay:
            s = 30
        else:
            continue
        scored.append((s, t))
    scored.sort(key=lambda x: (-x[0], x[1]["TableID"]))
    return {"matches": [{"table": t["TableID"], "name": t.get("TableName", ""),
                         "module": t["ModuleID"], "moduleName": t.get("ModuleName", ""),
                         "db": t.get("DB", "")} for _, t in scored[:limit]],
            "total": len(scored)}


def tool_get_table_structure(table: str):
    tid = (table or "").strip().upper()
    rows = [f for f in BUNDLE["fields"] if f["TableID"].upper() == tid]
    if not rows:
        return {"error": f"unknown table: {tid}", "hint": "use search_tables first"}
    pk = set(PK_MAP.get(tid, []))
    meta = next((t for t in BUNDLE["tables"] if t["TableID"].upper() == tid), {})
    rows.sort(key=lambda f: f.get("sID", ""))
    return {"table": tid, "name": meta.get("TableName", ""), "module": meta.get("ModuleID", ""),
            "columns": [{"column": f["ID"], "name": f.get("FieldName", ""), "type": f.get("Type", ""),
                         "length": f.get("Length"), "pk": f["ID"] in pk,
                         "description": f.get("Description", "")} for f in rows]}


def _bfs(start: str, goal: str):
    if start == goal:
        return []
    seen, queue = {start}, [(start, [])]
    while queue:
        node, path = queue.pop(0)
        for e in ADJ.get(node, []):
            nxt = e["to_table"]
            if nxt in seen:
                continue
            np = path + [{"from": e["from_table"], "to": nxt,
                          "on": [f"{a}={b}" for a, b in zip(e["from_columns"], e["to_columns"])],
                          "cardinality": e.get("cardinality", ""), "confidence": e.get("confidence", "")}]
            if nxt == goal:
                return np
            seen.add(nxt)
            queue.append((nxt, np))
    return None


def tool_get_join_path(tables: list):
    ids = [str(t).strip().upper() for t in (tables or []) if str(t).strip()]
    if len(ids) < 2:
        return {"error": "need >= 2 tables"}
    known = {t["TableID"].upper() for t in BUNDLE["tables"]}
    unknown = [t for t in ids if t not in known]
    if unknown:
        return {"error": f"unknown tables: {unknown}"}
    hops = []
    for a, b in zip(ids, ids[1:]):
        p = _bfs(a, b)
        if p is None:
            return {"error": f"no join path between {a} and {b}"}
        hops.extend(p)
    return {"path": hops, "tables": ids}


ERROR_HINTS = {
    "TABLE_REFERENCE_FORMAT_INVALID": "Every table reference must be bracketed: FROM [ACTMJ] or FROM [VPIC1].[dbo].[ACTMJ]; unbracketed names are rejected. Alias form: FROM [ACTMJ] MJ, then qualify columns as MJ.[MJ001] or [MJ].[MJ001].",
    "NO_TABLE_REFERENCE": "No FROM/JOIN table reference found in the statement.",
    "UNKNOWN_TABLE": "Table id not in ERP metadata — call search_tables to resolve the correct id first.",
    "UNKNOWN_TABLE_ALIAS": "Alias not declared in FROM/JOIN — check the alias list.",
    "UNKNOWN_COLUMN": "Column id not in ERP metadata — call get_table_structure for valid ids.",
    "UNKNOWN_COLUMN_FOR_TABLE": "Column exists in ERP but not on this table — check get_table_structure(table).",
    "NON_SELECT_INTENT": "Only SELECT is allowed (no INSERT/UPDATE/DELETE/DDL/EXEC).",
    "UNSUPPORTED_SQL2000_FEATURE": "SQL Server 2000 only: no CTE/WITH, no window functions (OVER/ROW_NUMBER/RANK), no OFFSET/FETCH, no EXCEPT/INTERSECT. Use TOP N for limits.",
    "MULTI_STATEMENT_NOT_ALLOWED": "Send exactly one SELECT statement; no semicolon-separated batches.",
}


def _err(source: str, code: str) -> dict:
    return {"source": source, "code": code, "hint": ERROR_HINTS.get(code, "")}


def tool_validate_sql(sql: str):
    errors, ok = [], True
    good, msg = sql2000_guard.validate_sql(sql)
    if not good:
        ok = False
        errors.append(_err("sql2000_guard", msg))
    good2, msg2 = metadata_validator.validate_metadata_references(sql, BUNDLE)
    if not good2:
        ok = False
        errors.append(_err("metadata_validator", msg2))
    return {"ok": ok, "errors": errors}


VPIC_PREFIX = re.compile(r"\[?VPIC1\]?\s*\.\s*\[?dbo\]?\s*\.\s*", re.IGNORECASE)


def tool_execute_sql(sql: str, max_rows: int = 200):
    v = tool_validate_sql(sql)
    if not v["ok"]:
        return {"error": "validation failed", "details": v["errors"]}
    safe_sql = VPIC_PREFIX.sub("dbo.", sql)
    body = {"queries": [{"refId": "A", "datasource": {"uid": DATASOURCE_UID, "type": "mssql"},
                         "rawSql": safe_sql, "format": "table"}], "from": "now-1y", "to": "now"}
    req = urllib.request.Request(f"{GRAFANA_URL}/api/ds/query", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {GRAFANA_TOKEN}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"error": f"grafana query failed: HTTP {exc.code}", "details": detail,
                "hint": "table may not be seeded in wferp_test yet (only ACTMI/ACTMJ/ACTMK exist)" if exc.code == 400 else ""}
    except Exception as exc:
        return {"error": f"grafana query failed: {exc}"}
    res = resp.get("results", {}).get("A", {})
    if res.get("status") != 200:
        return {"error": "query rejected", "details": res.get("error") or res}
    frames = res.get("frames", [])
    if not frames:
        return {"columns": [], "rows": [], "rowCount": 0}
    f0 = frames[0]
    cols = [f["name"] for f in f0["schema"]["fields"]]
    values = f0.get("data", {}).get("values", [])
    n = len(values[0]) if values else 0
    try:
        cap = max(1, min(int(max_rows), 1000))
    except (TypeError, ValueError):
        cap = 200
    rows = [[col[i] for col in values] for i in range(min(n, cap))]
    return {"columns": cols, "rows": rows, "rowCount": n, "truncated": n > cap,
            "executedSql": safe_sql}


GUIDELINES = {
    "dialect": "SQL Server 2000 compatible T-SQL",
    "rules": [
        "SELECT only — INSERT/UPDATE/DELETE/DDL/EXEC are rejected",
        "Single statement — no multi-statement batches",
        "No CTE (WITH ... AS), no window functions (OVER/ROW_NUMBER/RANK), no OFFSET/FETCH, no EXCEPT/INTERSECT",
        "Table and column identifiers MUST be bracketed: [ACTMJ], [MJ001]",
        "Qualify tables as [dbo].[TABLE] for this test datasource (the real ERP uses [VPIC1].[dbo].[TABLE]; execute_sql strips the VPIC1 prefix automatically)",
        "Use TOP N instead of LIMIT/OFFSET for row limits",
        "Give selected columns recognizable aliases: AS [可識別欄位名]",
    ],
    "coverage": {
        "metadata": "FULL Workflow ERP: 1,369 tables across 53 modules — search_tables/get_table_structure/get_join_path/validate_sql work for ALL of them",
        "execution": "the connected test database (wferp_test) currently materializes only a SUBSET of tables (ACTMI, ACTMJ, ACTMK); SQL for other tables still validates, but execute_sql will return an object-not-found error until more test data is seeded",
    },
    "naming_conventions": {
        "table_id": "3-char module prefix + 2-char entity + 1-char role letter (example: ACTMI/ACTMJ/ACTMK are 預算 module tables; COPTC is 訂單, PURLB is 採購單)",
        "column_id": "2-char table suffix + 3-digit sequence, e.g. table ACTMJ columns are MJ001, MJ002, ...",
    },
    "relationship_semantics": {
        "note": "Workflow ERP has NO database-level foreign keys; relationships are heuristic",
        "heuristic": "tables sharing the same 3-char prefix are related; join columns = primary keys aligned by position (shorter width wins); equal PK width => 1:1 else 1:N",
        "advice": "always fetch join paths via get_join_path instead of inventing ON conditions",
    },
    "workflow": [
        "search_tables(keyword) to pick tables from business terms",
        "get_table_structure(table) for columns and PKs",
        "get_join_path([tables]) for ON clauses when joining",
        "write the SELECT following these rules, then validate_sql (must pass)",
        "execute_sql to preview rows against the wferp_test database",
    ],
}


def tool_get_sql_guidelines():
    return GUIDELINES


FORMAT_RULES = (
    " Additional strict format rules: every table and column identifier MUST be bracketed "
    "([ACTMJ], [MJ001]); qualify tables as [VPIC1].[dbo].[TABLE]; aliases are unbracketed "
    "(FROM [VPIC1].[dbo].[ACTMJ] MJ) and qualified columns look like MJ.[MJ001]; "
    "use TOP N for row limits; give selected columns recognizable aliases AS [可識別欄位名]."
)


def _gen_prompt(user_prompt: str, context: dict, failed_sql: str = "", failed_reason: str = "") -> str:
    # wferp's build_llm_prompt omits the bracket convention and its repair prompt only gives
    # an opaque failure code — LLMs then can never satisfy metadata_validator. Same engine,
    # augmented prompt (this is the one deliberate deviation from the CLI path).
    base = llm_sql_generator.build_llm_prompt(user_prompt, context) + FORMAT_RULES
    if not failed_sql:
        return base
    hint = ERROR_HINTS.get(failed_reason, failed_reason)
    return (f"{base}\nPrevious SQL candidate failed validation. "
            f"Failure: {failed_reason} — {hint}.\nPrevious SQL: {failed_sql}\n"
            "Rewrite SQL to fix the failure and return JSON only with keys: sql, used_tables, assumptions, confidence.")


def _llm_generate(prompt: str, model: str, attempts: int = 4) -> dict:
    context = schema_context_builder.build_context_slice(prompt, BUNDLE)
    failed_sql, failed_reason = "", ""
    for _ in range(max(1, attempts)):
        try:
            raw = llm_sql_generator.call_llm(provider="openai-compatible", model=model,
                                             prompt_text=_gen_prompt(prompt, context, failed_sql, failed_reason),
                                             timeout_sec=60.0)
            out = llm_sql_generator.parse_llm_response(raw)
        except RuntimeError as exc:
            return {"error": str(exc)}
        sql = str(out.get("sql", "")).strip()
        ok1, code1 = sql2000_guard.validate_sql(sql)
        ok2, code2 = metadata_validator.validate_metadata_references(sql, BUNDLE)
        ok3, code3 = prompt_sql_consistency.validate_prompt_sql_consistency(prompt, sql)
        if ok1 and ok2 and ok3:
            return {"sql": sql, "route": "llm", "reason": "OK",
                    "used_tables": out.get("used_tables", []), "assumptions": out.get("assumptions", []),
                    "validation": tool_validate_sql(sql)}
        failed_reason = code1 if not ok1 else (code2 if not ok2 else code3)
        failed_sql = sql
    return {"error": f"LLM_REPAIR_FAILED:{failed_reason}", "note": "rule fallback disabled", "last_sql": failed_sql}


def tool_generate_sql(prompt: str, model: str = ""):
    p = str(prompt or "").strip()
    if not p:
        return {"error": "prompt is required"}
    return _llm_generate(p, model or DEFAULT_LLM_MODEL)


TOOLS = [
    {"name": "search_tables",
     "description": "Search Workflow ERP tables by keyword (table id like 'COPTC', Chinese/Vietnamese name, or module name). Returns table ids for use with get_table_structure/get_join_path.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "keyword: table id, business name (e.g. 採購單), or module"},
         "limit": {"type": "integer", "default": 20, "maximum": 100}}, "required": ["query"]}},
    {"name": "get_table_structure",
     "description": "Get columns (id, Chinese name, type, length, PK flags) for one Workflow ERP table id.",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "description": "table id, e.g. ACTMJ"}}, "required": ["table"]}},
    {"name": "get_join_path",
     "description": "Find join path between two or more Workflow ERP tables using inferred PK/FK relationships. Returns ordered hops with ON column pairs.",
     "inputSchema": {"type": "object", "properties": {
         "tables": {"type": "array", "items": {"type": "string"}, "description": "ordered table ids, e.g. [\"ACTMI\",\"ACTMJ\"]"}},
         "required": ["tables"]}},
    {"name": "validate_sql",
     "description": "Validate a SELECT statement against Workflow ERP rules. REQUIRES: bracketed identifiers for every table/column (FROM [ACTMJ] MJ, columns [MJ001] or MJ.[MJ001]); SELECT-only; SQL Server 2000-compatible (no CTE/window/OFFSET); single statement. ALWAYS call before execute_sql; on failure follow the returned per-error hints.",
     "inputSchema": {"type": "object", "properties": {
         "sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "execute_sql",
     "description": "Validate then execute a read-only SELECT via the Grafana MSSQL datasource (covers the FULL ERP metadata for validation; the wferp_test database currently holds only ACTMI/ACTMJ/ACTMK, other tables fail at execution until seeded). Returns columns+rows (default cap 200). Only SELECT is accepted; validation runs automatically first. Do not prefix tables with [VPIC1].",
     "inputSchema": {"type": "object", "properties": {
         "sql": {"type": "string"},
         "max_rows": {"type": "integer", "default": 200, "maximum": 1000}}, "required": ["sql"]}},
    {"name": "get_sql_guidelines",
     "description": "Read this FIRST when asked to write SQL for Workflow ERP: dialect rules (SQL Server 2000, SELECT-only, bracketed identifiers), table/column naming conventions, how table relationships work (heuristic, no real FKs), and the recommended tool workflow.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "generate_sql",
     "description": "Generate one Workflow ERP SELECT from a natural-language prompt using the same wferp engine (skill_scripts): intent parse -> LLM generation with repair -> full validation (sql2000_guard + metadata + prompt consistency). LLM-only; LLM failure returns an error (no rule fallback). Returns sql + validation + assumptions.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "natural-language data request, e.g. 查詢採購單前 20 筆"},
         "model": {"type": "string", "description": "LLM model (default from server env)"}},
         "required": ["prompt"]}},
]

HANDLERS = {"search_tables": lambda a: tool_search_tables(a.get("query", ""), a.get("limit", 20)),
            "get_table_structure": lambda a: tool_get_table_structure(a.get("table", "")),
            "get_join_path": lambda a: tool_get_join_path(a.get("tables", [])),
            "validate_sql": lambda a: tool_validate_sql(a.get("sql", "")),
            "execute_sql": lambda a: tool_execute_sql(a.get("sql", ""), a.get("max_rows", 200)),
            "get_sql_guidelines": lambda a: tool_get_sql_guidelines(),
            "generate_sql": lambda a: tool_generate_sql(a.get("prompt", ""), a.get("model", ""))}

SERVER_INFO = {"name": "wferp-mcp", "version": "0.1.0"}
PROTOCOL = "2025-03-26"


def rpc_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_rpc(msg: dict):
    method = msg.get("method", "")
    rid = msg.get("id")
    if method == "initialize":
        return rpc_result(rid, {"protocolVersion": PROTOCOL,
                                "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": SERVER_INFO})
    if method == "ping":
        return rpc_result(rid, {})
    if method == "tools/list":
        return rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        p = msg.get("params", {})
        name, args = p.get("name", ""), p.get("arguments", {}) or {}
        fn = HANDLERS.get(name)
        if not fn:
            return rpc_error(rid, -32602, f"unknown tool: {name}")
        try:
            out = fn(args)
        except Exception as exc:  # tool-level failure → MCP isError result
            return rpc_result(rid, {"content": [{"type": "text", "text": f"tool error: {exc}"}], "isError": True})
        return rpc_result(rid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                                "isError": bool(isinstance(out, dict) and out.get("error"))})
    if rid is None:  # notification (initialized, cancelled, ...)
        return None
    return rpc_error(rid, -32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/mcp", ""):
            self._send(405, {"error": "SSE streaming not supported; POST JSON-RPC only"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        # MCP streamable-http session termination; we are stateless, so just ack.
        self._send(200, {"ok": True})

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            return self._send(404, {"error": "not found"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except Exception:
            return self._send(400, rpc_error(None, -32700, "parse error"))
        msgs = payload if isinstance(payload, list) else [payload]
        replies = [r for m in msgs if (r := handle_rpc(m)) is not None]
        if not replies:
            return self._send(202)
        self._send(200, replies if isinstance(payload, list) else replies[0])

    def log_message(self, format, *args):  # noqa: A002 — base-class signature
        sys.stderr.write("wferp-mcp %s\n" % (format % args))


if __name__ == "__main__":
    print(f"wferp-mcp {SERVER_INFO['version']} on :{PORT} (WFERP_HOME={WFERP_HOME}, ds={DATASOURCE_UID})", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
