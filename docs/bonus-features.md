# Bonus features attempted

This list distinguishes implemented code from live evidence; it is not a claim that every optional capability is production-ready.

| Feature | Evidence in this repository/demo | Status and limits |
| --- | --- | --- |
| Gemini-backed chat agent with multi-round tool calling | `docs/chat-agent.md`, gateway `/assistant`, mocked-provider tests, and live successful User A requests | Live provider requests succeeded for product creation, today's orders, and a two-upstream read. External provider availability can still fail with a sanitized 503. |
| MCP Streamable HTTP endpoint | Terminal `curl` request to the HTTPS `/mcp` endpoint using `X-API-Key` and an MCP `initialize` JSON-RPC request; earlier SSH-tunnel MCP-client checks covered `tools/list` and read calls | Terminal connectivity was tested; the successful POST command and response were not retained in the available PowerShell history, so no HTTP result is claimed here. Claude Code was not tested. Upstream MCP Services remain server-side. |
| Upstream registry management and live status | Admin API and dashboard; live reversible Support disable and outage checks | Statuses observed as Disabled, Unreachable, and Connected. Keep upstream registrations internal and admin-only. |
| Bounded tool-call deadlines | Gateway client, timeout-specific REST/MCP errors, audit fields, and regression tests | Automated timeout handling is tested. The Support outage test verified per-upstream isolation; no live artificial slow response was induced. |
| Aggregate tool-call observability | Admin-only endpoint and dashboard panel in source/build | Availability depends on current dashboard bundle and access. Metrics are aggregate only; p95 is not included. |
| OAuth authorization-server work | OAuth code, migrations, and docs exist in the working tree | Do not present as a completed public OAuth deployment without a separate live protocol and security review. API-key auth remains the verified assessment path. |
| Helm upgrade/rollback runbook and k3s deployment guide | `docs/helm-upgrades-rollback.md` and `docs/vps-k3s-deployment.md`; Azure charts/releases deployed | Commands must be checked against current values/revisions before reuse; local test results do not prove a future upgrade. |

The required core remains more important than these extensions: role-safe tool access, complete two-user workflow, persistence, private upstreams, and a transport-secure public deployment.
