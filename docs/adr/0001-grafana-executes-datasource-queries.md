# Grafana Executes Datasource Queries

Accepted. Grafana is the only component that executes datasource queries. The Grafana Query MCP resolves an authorized dataset's server-owned datasource and query template, enforces session and response bounds, and calls Grafana's `/api/ds/query` endpoint. Model-authored SQL, URLs, credentials, and direct datasource connections are not accepted. No MCP has an execution fallback.

## Considered Options

- Let MCP servers execute SQL or directly connect to databases: rejected because it duplicates Grafana datasource connectors, bypasses Grafana permissions/audit boundaries, and expands the security surface.
- Keep an MCP SQL execution fallback: rejected because fallback paths tend to become production paths and weaken the boundary.
