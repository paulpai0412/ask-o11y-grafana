# Retired WFERP MCP

The standalone WFERP MCP runtime was removed during the adaptive Ask O11y migration. It is not an endpoint, has no server entrypoint, and must not be registered or started.

The adaptive platform still has only four external MCP endpoints: Data Query Planner, Grafana Query, isolated Sandbox Analysis, and the hidden Artifact Bridge; Ask O11y's built-in Grafana MCP is the sole Dashboard writer. WFERP is now an authorized dataset through those existing boundaries, not a restored standalone MCP.

WFERP keeps the former `llm-first` behavior with one intentional boundary change: Ask O11y's existing runtime LLM authors one SQL Server SELECT from a bounded schema context returned by Data Query Planner. Data Query Planner validates policy, metadata references, prompt consistency, and database scope before creating an opaque immutable plan. Grafana Query is the only executor and sends that plan through Grafana `/api/ds/query`; no MCP directly connects to MSSQL and no embedded LLM provider was restored.
