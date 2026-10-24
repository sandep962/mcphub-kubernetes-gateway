# MCPHub threat model

## Assets

- API-key plaintext during issuance and authentication; stored API-key hashes and prefixes.
- Identity roles, team memberships, and explicit tool grants.
- WooCommerce consumer credentials, Gemini provider key, database credentials, and Kubernetes Secrets.
- WooCommerce orders/products, Inventory data, Support tickets, and audit metadata.
- Tool descriptions, schemas, arguments, and results crossing the gateway/model boundary.

## Trust boundaries

1. **Client to gateway:** Browser or MCP clients supply an API key. The gateway validates it per request, resolves identity/role from metadata storage, and does not trust client-supplied identity fields.
2. **Gateway to upstream:** Only gateway workloads should call MCP upstream Services. Shared upstream credentials are injected server-side; clients and the LLM receive tool definitions/results, never those credentials.
3. **Gateway to Gemini:** The model provider is external and receives prompts plus authorized tool schemas/results. A model proposal is untrusted and is parsed, allowlisted against the authenticated user's catalog, and re-authorized by the gateway before execution.
4. **Kubernetes to data stores:** WordPress/MariaDB and MCP metadata are separate. Kubernetes Secret access and node/root access are privileged trust boundaries.
5. **Public ingress:** Azure demo routes use HTTPS with a cert-manager-managed certificate and HTTP-to-HTTPS redirects. Certificate validity and HTTPS endpoint health were checked on 2026-10-24. The deployment is still a demo environment; DNS, certificate renewal, cluster administration, host/browser compromise, and single-node availability remain operational risks.

## Threats and controls

| Threat | Controls | Residual risk |
| --- | --- | --- |
| Theft/replay of a client API key | High-entropy generated keys; SHA-256 hash and prefix storage; per-request validation; revocation; key held in dashboard tab memory; HTTPS ingress with HTTP-to-HTTPS redirect | Any key sent over cleartext HTTP is compromised; rotate it. HTTPS configuration reduces network interception risk but does not mitigate host/browser malware or trusted-cluster administrator access. |
| Member gains admin access through team assignment | Backend checks database-derived role on every admin route; team membership is not treated as admin; Viewer/Operator default-deny tool authorization | Review every new route and tool; UI hiding is not an authorization control. |
| Shared WooCommerce/Gemini credentials leak to clients | Credentials are Kubernetes Secret references consumed server-side; MCP clients call only the gateway; API responses redact upstream endpoint and credential fields | Kubernetes administrators/node root can access Secrets or pod environments. Use least-privilege RBAC, encryption at rest, and secret rotation. |
| Prompt injection through upstream tool descriptions/results | Tool descriptions/results are treated as data; only tools in the authorized catalog are offered; all proposed calls are allowlisted and re-authorized; tool deadlines apply | The model may still produce misleading text. Validate side effects and avoid giving the model tools beyond the user's explicit grants. |
| Malicious or compromised MCP upstream | Upstreams are registered by admins; tool routing is namespaced, authorization is repeated at execution, errors are sanitized, calls have bounded deadlines, and upstream credentials stay in the server-side workload | An authorized upstream can still return false/malicious data or perform side effects exposed by its tools. Review upstream code, schemas, permissions, and endpoint changes before enabling it; do not treat tool output as trusted instructions. |
| Tool-name collision or confused routing | Gateway namespaces client tool names with upstream names and rejects ambiguous routing; upstream registrations are admin-only | Upstream registration changes can alter available schemas; review before enabling a server. |
| Upstream outage or slow call blocks all users | Per-upstream discovery/status isolation and bounded call timeout; sanitized timeout errors and audit outcomes | Gateway/provider/metadata DB remain shared dependencies; local-path single-node Kubernetes has no HA guarantee. |
| Audit data leaks customer payloads or secrets | Audit records store identity, server/tool, outcome, duration, and sanitized error class; no raw tool arguments/results or API keys | Audit metadata still reveals usage patterns and should be access-controlled and retained only as needed. |
| Database or PVC loss | Separate databases/PVCs, backup and restore procedures, versioned migrations | PVC persistence is not backup; recovery remains operator-managed and tested backups are required. |

## Operational requirements

- Keep HTTPS termination, certificate renewal, and HTTP-to-HTTPS redirect healthy before accepting credentials over the public network; recheck certificate and route status before demos.
- Restrict public exposure to intended dashboard, gateway, and storefront routes; keep all MCP upstream and database Services internal.
- Restrict who can read Secrets, exec into pods, or administer the node; enable Kubernetes Secret encryption at rest.
- Maintain protected, off-node database backups and test restore to a separate target before relying on them.
- Revoke and replace any credential suspected of being sent over cleartext HTTP; verify the old credential receives 401 afterward.
- Keep synthetic checkout/order data separate from real customer data in the demo.
