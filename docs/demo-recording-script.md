# MCPHub assignment demo recording script

This is a recording plan, not a recorded video. The VPS/K3s path has not been deployed or live-tested.

## Before recording

- Start the existing Kind cluster port forwards with `./scripts/start-local-ports.ps1`.
- Add `127.0.0.1 storefront.mcphub.test dashboard.mcphub.test` to the Windows hosts file as described in [local-ingress.md](local-ingress.md), or use `curl --resolve` for terminal-only checks.
- Use existing admin and member keys from their protected local locations. Do not show, paste, or record key values, Kubernetes Secret values, or customer data.
- Use the existing demo product #30 and COD order #31. Do not create duplicate products/orders or change order #31.

## Recording sequence

1. Open the storefront and dashboard through their named Ingress URLs. Show the gateway health endpoint and MCP Inspector connected only to the gateway's Streamable HTTP URL.
2. Sign into the dashboard as admin. Show the three upstream registrations and the tool catalogue; do not reveal endpoint URLs, credentials, or key material.
3. In the chat, ask what the assistant can do, then ask for a read-only inventory lookup and a request that combines Inventory and Support. Show the tool names, outcomes, and audit attribution. Continue only after the configured Gemini provider returns successful responses.
4. Ask the assistant to list today's WooCommerce orders and retrieve order #31. Show its status, COD method, total, and the demo product name without showing billing/customer information.
5. Show the existing product #30 in the product list and storefront. For a create-product demonstration, use a uniquely dated demo name only after confirming no duplicate exists and agreeing how the disposable product will be cleaned up.
6. Sign into MCP Inspector with a Viewer key, run `initialize`, `tools/list`, and an allowed Inventory read. Attempt the admin-only upstream listing and show the 403 denial and matching audit event.
7. Show a previously revoked key rejected with 401. Do not revoke an active key during recording.
8. If demonstrating upstream disable/failure isolation, schedule a maintenance window, disable only a disposable upstream registration or use a controlled test upstream, show discovery and call behavior, then restore the original registration and verify all three upstreams are healthy.
9. End with the Python test result, dashboard lint/build, Helm lint/render, and the live Kind pod/PVC health summary. State clearly that VPS deployment and the video itself are not verified until actually completed.
