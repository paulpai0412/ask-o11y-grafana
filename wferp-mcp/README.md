# wferp-mcp

MCP (streamable-http) server that exposes Workflow ERP metadata + validated SQL execution to MCP clients (ask-o11y).

Thin adapter — the engine lives in `../wferp/skill_scripts` (schema_loader / sql2000_guard / metadata_validator). Stdlib only, no pip dependencies.

## Tools

| Tool | Purpose |
| --- | --- |
| `search_tables` | keyword → ERP table ids (TableID / 中文名 / 越文名 / module) |
| `get_table_structure` | table id → columns (id, name, type, PK flags) |
| `get_join_path` | ordered tables → join hops with ON column pairs (heuristic PK alignment) |
| `validate_sql` | SELECT-only + SQL Server 2000 guard + metadata reference check |
| `execute_sql` | validate → execute via Grafana `/api/ds/query` (wferp_test datasource) |
| `get_sql_guidelines` | dialect rules / naming conventions / relationship semantics — read first |
| `generate_sql` | natural-language → SELECT,LLM-only(wferp 引擎:context slice → LLM → 修復迴圈 → 三道驗證);失敗直接報錯,無 rule fallback |

`generate_sql` 為 LLM-only(2026-08-05 起移除 rule mode)。同次已回修 wferp 上游缺陷:`build_llm_prompt` 補上 bracket 格式規則、repair prompt 附可讀 hint、confidence 支援缺省(None→跳過閘門)與文字等級(high→0.9 等)——修復前 CLI 的 llm mode 對不回數值 confidence 的模型必敗(LOW_CONFIDENCE / TABLE_REFERENCE_FORMAT_INVALID)。

Metadata covers the **full ERP** (1,369 tables / 53 modules). `execute_sql` is limited by what is physically seeded in `wferp_test` (currently ACTMI/ACTMJ/ACTMK).

## Run

```bash
# prerequisites: Grafana SA token at ../.scratch/t2/sa-token.txt (or set GRAFANA_TOKEN)
python3 server.py            # listens on :8765
```

Env: `WFERP_MCP_PORT` (8765), `GRAFANA_URL` (<http://localhost:3000>), `GRAFANA_TOKEN` / `GRAFANA_TOKEN_FILE`, `MSSQL_DATASOURCE_UID`, `WFERP_HOME` (../wferp).

Endpoint: `POST /mcp` (JSON-RPC; plain JSON responses, no SSE).

## Register in ask-o11y

Plugin settings → MCP → add external server, or via API:

```bash
curl -u admin:admin -X POST localhost:3000/api/plugins/consensys-asko11y-app/settings \
  -H 'Content-Type: application/json' -d '{"enabled":true,"pinned":true,"jsonData":{
    "useBuiltInMCP":true,
    "mcpServers":[{"id":"wferp","name":"wferp ERP","url":"http://localhost:8765/mcp",
                   "type":"streamable-http","enabled":true,"trusted":true}]}}'
```

Health: `GET /api/plugins/consensys-asko11y-app/resources/api/mcp/servers` → `status: healthy`.
