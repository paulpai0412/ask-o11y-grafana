# Retired WFERP MCP

The standalone WFERP MCP runtime was removed during the adaptive Ask O11y migration. It is not an endpoint, has no server entrypoint, and must not be registered or started.

This repository retains the upstream `wferp/` reference material only. The adaptive platform has four MCP endpoints: Data Query Planner, Grafana Query, isolated Sandbox Analysis, and Grafana Renderer. Any future WFERP dataset support must enter through the same authorized Grafana metadata/query boundaries and requires a new reviewed goal; it must not restore direct SQL execution, an LLM SQL provider, or a standalone WFERP MCP.
