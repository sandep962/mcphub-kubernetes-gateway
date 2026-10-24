# Dashboard chat assistant

The dashboard assistant uses an OpenAI Chat Completions-compatible provider from the gateway only. The browser sends the signed-in user's normal `X-API-Key` to the authenticated gateway `/assistant` endpoint. The browser never receives the model API key. The gateway discovers the currently configured upstream tools, filters them through the user's existing permissions, and offers only that tool catalog to the model. Each selected tool is invoked by the gateway MCP router, which checks permissions again and records the call in the audit log under the authenticated identity.

The agent supports up to eight rounds of tool calls in one request. The UI shows each called tool and whether it succeeded or failed. Asking “What can I ask you?” asks the model for examples based on the tools currently available to that identity.

## MCP client tool names

The Streamable HTTP gateway namespaces discovered tools as `server__tool`, for example `inventory__list_inventory`, so same-named tools from different upstreams remain distinct. The REST `/call` endpoint accepts the upstream's native tool name and returns HTTP 409 when that name is ambiguous; it never routes an ambiguous call to an arbitrary server.

## Required server-side configuration

Configure all three values on the gateway server; do not put the API key in dashboard JavaScript, browser storage, or a Vite `VITE_` variable:

- `MCPHUB_LLM_BASE_URL`: OpenAI-compatible API base URL, including its API prefix when required (for example, `https://provider.example/v1`). The gateway appends `/chat/completions`.
- `MCPHUB_LLM_MODEL`: provider model identifier that supports function/tool calling.
- `MCPHUB_LLM_API_KEY`: provider API key.

When any value is absent, `/assistant` responds with HTTP 503 and a setup message; it does not fall back to deterministic intent matching. The gateway sends the user's conversation and authorized tool results to the configured provider to generate the answer. Never include secrets in chat prompts.

For the Helm deployment, set `llm.baseUrl` and `llm.model` in a private/non-secret values file and create the `mcphub-llm-secret` Kubernetes Secret with key `api-key` through the approved secret-management workflow. The chart reads that key as `MCPHUB_LLM_API_KEY`; it remains optional so existing installs continue to run with a clear unconfigured response. Keep the gateway's egress policy restricted to the selected provider endpoint using the cluster's network-policy capabilities. Standard Kubernetes NetworkPolicy has no portable FQDN allowlist, so do not add unrestricted egress merely to enable the assistant.

For local development, set the same three process environment variables in the shell before starting the gateway. Restart the gateway process after configuration changes.
