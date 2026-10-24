# MCPHub assignment demo recording checklist

This is a recording checklist, not a recorded video. The live environment is the Azure k3s demo in namespace `mcphub`, with HTTPS Ingress for the dashboard, gateway, and storefront at `sandeep-mcp-demo.duckdns.org`; cert-manager manages the TLS certificate and HTTP redirects to HTTPS. Use the HTTPS URLs for sign-in and MCP requests. Show only synthetic customer/order data. Do not record credentials, key prefixes, Secret values, browser storage, or customer PII.

## Before recording

- Confirm the Azure context, expected images, Ready replicas, gateway health/readiness, three upstream statuses, and Bound MariaDB/WordPress PVCs. Use [helm-upgrades-rollback.md](helm-upgrades-rollback.md) for release checks.
- Confirm `https://sandeep-mcp-demo.duckdns.org/dashboard` and `https://sandeep-mcp-demo.duckdns.org/mcp` are healthy and the certificate hostname/chain validate. Configure MCP Inspector for Streamable HTTP with the HTTPS MCP URL and the test user's own `X-API-Key`. Do not send keys to an HTTP URL. An SSH tunnel may be used as an alternative if public access is unavailable.
- Use protected, individually issued admin, User A, and User B credentials. Do not display or record the key entry screen, local secret files, or API-key text.
- The current demo product is **URUMI Round 1 User A Chat Demo 20261024-172025** (product ID 40, ₹1). Its COD storefront order is **#41** (processing, COD, ₹1). Existing order #38 is a separate earlier test. Do not create duplicate products/orders or change either order.
- The WooCommerce storefront is available on the HTTPS Ingress. Use only synthetic details if showing checkout; do not use real personal information or payment credentials.

## Recording sequence

1. Show the dashboard overview: gateway ready, three upstreams Connected, and the current authorized tool catalog. Avoid showing endpoint URLs or sensitive audit fields.
2. Show persisted `Administrators` and `Verification Team`, identities and roles. Demonstrate that Viewer/Operator access is distinct from admin access; show an admin-only endpoint denied to a member.
3. As User A, show the Gemini assistant's successful `woocommerce__create_product` call for product #40 and that the product is publicly viewable. Then ask for today's orders; show order #41, processing, COD, ₹1. Do not show billing/shipping details.
4. As User B, connect through the single gateway Streamable HTTP endpoint, run `initialize` and `tools/list`, and show authorized tools across Inventory, Support, and WooCommerce. Run a permitted read such as Inventory list and retrieve order #41. Never connect the client directly to an upstream.
5. Show one assistant prompt that calls tools from Inventory and WooCommerce, with both successful tool-call outcomes visible. The result should be from a run where both calls actually succeeded.
6. Show a denied Viewer mutation and its audit entry with identity ID, server/tool, denied outcome, and duration. Show a revoked test key returning 401 only if using a specifically issued and already revoked test key.
7. Show the controlled failure evidence: Support was temporarily scaled to zero, the status endpoint reported Support Unreachable, Inventory and WooCommerce remained Connected and served reads, then Support was restored to two Ready replicas and Connected. Do not perform this again during recording unless a dedicated maintenance window is available.
8. Explain the Kubernetes/Helm design: Secret references are shown by Secret/key names only; WordPress and MariaDB use Bound PVCs; MCP and database Services are ClusterIP; Deployments have readiness/liveness probes and CPU/memory requests/limits; Traefik routes selected applications over HTTPS using cert-manager-managed TLS and redirects HTTP to HTTPS. Show how the Helm upgrade/rollback runbook preserves releases and data. Never expose Secret values, pod environment values, raw logs, or connection strings.
9. End with exact Python test, dashboard lint/build, Helm lint/render, and diff-check results. State that the HTTPS ingress was checked on 2026-10-24, that the demo remains a single-node educational deployment rather than a production service, and that the submission video itself has not yet been recorded.

## Still to demonstrate visually

Automated clipboard success/fallback tests exist, but the real dashboard Copy key interaction has not been exercised in the browser in this session. Team persistence and backend role checks have live API evidence; the signed-in visual team dropdown was not browser-verified here. Do not claim either visual interaction was shown unless the recording verifies it.
