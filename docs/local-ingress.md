# Local MCPHub Ingress

This local setup uses Traefik chart `42.0.0` as the Kubernetes Ingress controller. The cluster's Kind node publishes only the Kubernetes API port, so the controller Service remains `ClusterIP`; `scripts/start-local-ports.ps1` forwards local port 8081 to that controller Service. Requests still enter Traefik and are routed by the Kubernetes Ingress rules. A hostless storefront Ingress rule preserves the existing `http://127.0.0.1:8081` local URL.

Install or update the controller and the MCPHub route resources with:

```powershell
helm repo add traefik https://traefik.github.io/charts
helm repo update traefik
helm upgrade --install traefik traefik/traefik --version 42.0.0 `
  --namespace traefik-system --create-namespace `
  --values .\infra\traefik\values.yaml --skip-crds --wait
helm upgrade --install mcphub-ingress .\infra\helm\mcphub-ingress `
  --namespace mcphub --wait
```

The Traefik release has a dedicated, non-default IngressClass and watches only `mcphub`. Gateway API and Traefik CRD providers are disabled. `--skip-crds` avoids installing unused CRDs from the upstream chart.

The store Helm release is installed in `default`, but the store chart's namespace value places WordPress, MariaDB, and their PVCs in `mcphub`. The Ingress resources therefore live in `mcphub`, the same namespace as both backend Services:

- `http://storefront.mcphub.test:8081` → `mcphub/mcphub-wordpress:80`
- `http://dashboard.mcphub.test:8081` → `mcphub/mcphub-dashboard:8080`

Windows does not resolve arbitrary `.test` names automatically. Add these lines to `%SystemRoot%\System32\drivers\etc\hosts` from an elevated editor, or use the `curl.exe --resolve` commands below for verification without changing the hosts file:

```text
127.0.0.1 storefront.mcphub.test dashboard.mcphub.test
```

To undo this local name mapping, reopen the hosts file as Administrator and remove that exact line (or remove only the two `.test` host entries if the line has been combined with other mappings), then save the file. No cluster resources need to be changed.

After starting the local port forwards, verify routing with:

```powershell
curl.exe --resolve storefront.mcphub.test:8081:127.0.0.1 http://storefront.mcphub.test:8081/
curl.exe --resolve dashboard.mcphub.test:8081:127.0.0.1 http://dashboard.mcphub.test:8081/
```

Start the forwards with `.\scripts\start-local-ports.ps1` and stop them with `.\scripts\stop-local-ports.ps1`.

The local URLs use HTTP for this development cluster. No TLS certificate or Kind host-port mapping is configured. The dashboard (`5174`) and gateway (`8010`) keep their existing direct service forwards. Port `8081` now forwards to Traefik, which routes both named hosts and the legacy bare-IP storefront request through Ingress.

WordPress currently reports `http://localhost:8081` as its canonical home URL. The storefront root returns HTTP 200 through the named Ingress host, while `/wp-admin/` redirects to the canonical localhost URL. The admin redirect behavior has not been changed; keep WooCommerce OAuth's canonical URL aligned with the WordPress home URL until that store configuration is deliberately migrated.
