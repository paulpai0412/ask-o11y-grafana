# Grafana Executes Datasource Queries

Accepted. Data Query Planner MCP creates and validates datasource query plans from authorized metadata, but Grafana is the only component that executes those queries against datasources. For most registered datasets the query is deterministic metadata-derived. For the authorized WFERP dataset, Ask O11y's existing runtime LLM authors one SQL Server SELECT from a bounded planner-provided schema context; Data Query Planner applies read-only SQL policy, metadata whitelist, prompt-consistency, and database-scope validation before storing an opaque immutable plan. Grafana Query alone executes that plan through `/api/ds/query`. No MCP receives database credentials, directly connects to MSSQL, or has an execution fallback.

## Considered Options

- Let MCP servers execute SQL or directly connect to databases: rejected because it duplicates Grafana datasource connectors, bypasses Grafana permissions/audit boundaries, and expands the security surface.
- Keep an MCP SQL execution fallback: rejected because fallback paths tend to become production paths and weaken the boundary.
