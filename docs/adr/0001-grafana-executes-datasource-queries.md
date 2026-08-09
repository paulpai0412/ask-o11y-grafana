# Grafana Executes Datasource Queries

Accepted. Data Query Planner MCP generates and validates datasource queries from metadata, but Grafana is the only component that executes those queries against datasources. Firepower analysis MCP receives already-queried Grafana DataFrame JSON and never direct database credentials, SQL execution responsibility, or an execution fallback; this keeps datasource auth, permissions, auditing, and connector behavior inside Grafana instead of duplicating them in MCP servers.

## Considered Options

- Let MCP servers execute SQL or directly connect to databases: rejected because it duplicates Grafana datasource connectors, bypasses Grafana permissions/audit boundaries, and expands the security surface.
- Keep an MCP SQL execution fallback: rejected because fallback paths tend to become production paths and weaken the boundary.
