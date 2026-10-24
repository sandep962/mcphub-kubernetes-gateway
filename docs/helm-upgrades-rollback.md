# MCPHub Helm upgrade and rollback procedures

This runbook covers the existing WooCommerce store, three MCP server releases, and gateway. It is written for the current Kind installation and uses the repository charts. Never uninstall a release, delete a PVC, or recreate the cluster as a recovery step.

## Current release inventory (inspection snapshot)

Release revisions and images below were read from the existing cluster on 2026-10-22. Recheck them before every operation; revisions advance and image tags change.

| Component | Helm release | Chart | Release namespace | Workload namespace | Snapshot revision | Deployed image(s) |
|---|---|---|---|---|---:|---|
| Store | `mcphub-store` | `infra/helm/mcphub-store` | `default` | `mcphub` | 3 | WordPress `mcphub-wordpress:local`; MariaDB `mariadb:11.4` |
| WooCommerce MCP | `mcphub-woocommerce-mcp` | `infra/helm/mcphub-woocommerce-mcp` | `mcphub` | `mcphub` | 5 | `mcphub-woocommerce-mcp:verify-order-payment-method` |
| Inventory MCP | `mcphub-inventory-mcp` | `infra/helm/mcphub-inventory-mcp` | `mcphub` | `mcphub` | 5 | `mcphub-inventory-mcp:persistent-v2` |
| Support MCP | `mcphub-support-mcp` | `infra/helm/mcphub-support-mcp` | `mcphub` | `mcphub` | 5* | `mcphub-support-mcp:persistent-v2` |
| Gateway | `mcphub-gateway` | `infra/helm/mcphub-gateway` | `mcphub` | `mcphub` | 28 | `mcphub-gateway:observability-rebuild-20261022-v1` |

The store release being in `default` while its resources are rendered into `mcphub` is intentional (`namespace: mcphub` in its values). The dashboard and ingress are separate releases and are not changed by these procedures.

\* Support was at revision 3 when inspected. The safe no-op lifecycle check described at the end created revision 4 and rolled back to revision 3, producing the current revision 5. The deployed manifest and pod template remained unchanged.

## Prerequisites and preservation

Run commands from `C:\MCPHub` in PowerShell. Confirm the intended context and inspect current state first:

```powershell
kubectl config current-context
helm list -A
kubectl get deployments,statefulsets,pvc -n mcphub
```

For an application image update, build the new image from the repository's documented Dockerfile/context, use a unique tag, and load it into the existing cluster before upgrading:

```powershell
docker build -f <Dockerfile> -t <image>:<new-tag> <build-context>
kind load docker-image <image>:<new-tag> --name kind
```

Do not use `latest`. Confirm the chart's rendered image field and any configuration changes before running `helm upgrade`. Use the release's existing effective settings with `--reuse-values`; do not run these commands with chart defaults or `--reset-values`.

Before any WordPress or MariaDB change, verify both store PVCs are Bound and take/verify a database backup using the approved store backup procedure. Do not change MariaDB major/minor versions as part of a routine WordPress image upgrade. MariaDB StatefulSet/PVC templates and Secret references are data-critical; never delete/recreate them to recover an upgrade. Helm rollback cannot restore data already changed by a database migration.

Do not put passwords, tokens, API keys, Secret data, or a complete secret-bearing values dump in command history or a report. Inspect only allowlisted, non-secret fields from Helm values. For example, this prints only image/replica/runtime fields (never the LLM Secret value):

```powershell
$v = helm get values mcphub-gateway -n mcphub --all -o json | ConvertFrom-Json
[pscustomobject]@{ Image=$v.image; Replicas=$v.replicas; ToolTimeout=$v.upstreamToolCallTimeoutSeconds; LlmBaseUrl=$v.llm.baseUrl; LlmModel=$v.llm.model; LlmSecretName=$v.llm.apiKeySecretName; LlmSecretKey=$v.llm.apiKeySecretKey }
$v = helm get values mcphub-store -n default --all -o json | ConvertFrom-Json
[pscustomobject]@{ WorkloadNamespace=$v.namespace; WordPressImage=$v.wordpress.image; WordPressReplicas=$v.wordpress.replicas; MariaDbImage=$v.mariadb.image; WordPressStorage=$v.wordpress.storage; MariaDbStorage=$v.mariadb.storage }
```

Use equivalent explicit allowlists for MCP charts (`image`, `replicas` or `replicaCount`, and non-secret URL settings). Do not print the unfiltered object. Check deployed image tags independently:

```powershell
kubectl get deployment mcphub-gateway -n mcphub -o jsonpath='{.spec.template.spec.containers[*].image}'
kubectl get deployment mcphub-woocommerce-mcp -n mcphub -o jsonpath='{.spec.template.spec.containers[*].image}'
kubectl get deployment mcphub-inventory-mcp -n mcphub -o jsonpath='{.spec.template.spec.containers[*].image}'
kubectl get deployment mcphub-support-mcp -n mcphub -o jsonpath='{.spec.template.spec.containers[*].image}'
kubectl get deployment mcphub-wordpress -n mcphub -o jsonpath='{.spec.template.spec.containers[*].image}'
kubectl get statefulset mcphub-mariadb -n mcphub -o jsonpath='{.spec.template.spec.containers[*].image}'
```

## Upgrade commands

Set `$tag` to an image that has already been built, loaded, and reviewed. These commands update only the target image setting and preserve all other release values. The store's routine command updates WordPress only; MariaDB is deliberately excluded.

```powershell
# WooCommerce store (WordPress image only; Helm release namespace is default)
$tag = 'mcphub-wordpress:<new-tag>'
helm upgrade mcphub-store .\infra\helm\mcphub-store -n default --reuse-values --set "wordpress.image=$tag" --wait --timeout 5m

# WooCommerce MCP
$tag = 'mcphub-woocommerce-mcp:<new-tag>'
helm upgrade mcphub-woocommerce-mcp .\infra\helm\mcphub-woocommerce-mcp -n mcphub --reuse-values --set "image=$tag" --wait --timeout 5m

# Inventory MCP
$tag = 'mcphub-inventory-mcp:<new-tag>'
helm upgrade mcphub-inventory-mcp .\infra\helm\mcphub-inventory-mcp -n mcphub --reuse-values --set "image=$tag" --wait --timeout 5m

# Support MCP
$tag = 'mcphub-support-mcp:<new-tag>'
helm upgrade mcphub-support-mcp .\infra\helm\mcphub-support-mcp -n mcphub --reuse-values --set "image=$tag" --wait --timeout 5m

# MCP gateway
$tag = 'mcphub-gateway:<new-tag>'
helm upgrade mcphub-gateway .\infra\helm\mcphub-gateway -n mcphub --reuse-values --set "image=$tag" --wait --timeout 5m
```

For MariaDB image maintenance, use a separately reviewed change with a verified backup, compatibility review, and explicit approval. Never casually set `mariadb.image`; the StatefulSet restart can affect every application using the database. Do not change store Secret values through Helm.

Before a live command, render the exact chart and intended image override and inspect the manifest diff against the live resources. Verify the only intended pod-template change is the image. For the store, confirm no change to the MariaDB StatefulSet, `mcphub-wordpress-pvc`, `data-mcphub-mariadb-0`, storage requests, Secret references, or services. If the diff includes anything else, stop. `helm upgrade --dry-run` is useful for validation but may include Secret-bearing rendered objects; do not print or share its full output.

## Rollback

First identify the current revision and a known-good prior deployed revision. Review `helm history` and inspect that revision's manifest/configuration without printing Secret data. Do not assume the immediately previous revision is safe: the gateway has a recorded failed revision, and store history includes an earlier failed revision.

```powershell
helm history mcphub-store -n default
helm history mcphub-woocommerce-mcp -n mcphub
helm history mcphub-inventory-mcp -n mcphub
helm history mcphub-support-mcp -n mcphub
helm history mcphub-gateway -n mcphub
```

Substitute the reviewed revision number (not the current one):

```powershell
helm rollback mcphub-store <known-good-revision> -n default --wait --timeout 5m
helm rollback mcphub-woocommerce-mcp <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-inventory-mcp <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-support-mcp <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-gateway <known-good-revision> -n mcphub --wait --timeout 5m
```

At the initial inspection snapshot, the immediately prior revisions were store `2`, WooCommerce `4`, Inventory `4`, Support `2`, and gateway `27`. Support revision `3` was separately verified by a no-op rollback and is currently represented by deployed revision `5`. Treat all older revisions only as historical candidates: verify their manifests/images and compatibility with the current database before using them. Rollback creates a new Helm revision; it does not erase history. Rollback restores Kubernetes configuration/images, not WordPress/MariaDB data. Never use `helm uninstall` or delete PVCs.

## Post-upgrade and post-rollback verification

Wait for the release's workloads and check image, readiness, store persistence, and gateway endpoints:

```powershell
kubectl rollout status deployment/mcphub-wordpress -n mcphub --timeout=5m
kubectl rollout status statefulset/mcphub-mariadb -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-woocommerce-mcp -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-inventory-mcp -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-support-mcp -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-gateway -n mcphub --timeout=5m
kubectl get deployments,statefulsets,pods,pvc -n mcphub -o wide
```

For a WordPress-only store update, also confirm the MariaDB pod did not restart and both claims remain Bound:

```powershell
kubectl get pvc -n mcphub
kubectl get pod mcphub-mariadb-0 -n mcphub -o wide
```

Check application health through the existing local-forward workflow or the approved Ingress. Do not assume a port-forward is running. The gateway endpoints are `/health` and `/ready`; the MCP HTTP endpoint is `/mcp`. A safe authenticated upstream-status check should return the configured upstream names and statuses without URLs or credentials. Then issue a harmless read-only `tools/list`/Inventory call through the gateway and confirm WooCommerce and Support remain discovered/reachable. For store changes, also confirm the storefront loads and existing products/orders are still present using read-only checks. Do not use a create/update/delete operation for routine upgrade verification.

```powershell
helm status mcphub-store -n default
helm status mcphub-woocommerce-mcp -n mcphub
helm status mcphub-inventory-mcp -n mcphub
helm status mcphub-support-mcp -n mcphub
helm status mcphub-gateway -n mcphub
```

Compare the live image tag, ready replica count, probes, resource settings, service, PVC status, and relevant safe configuration to the reviewed target. Check gateway logs for sanitized errors; never dump environment variables or Secret objects.

## Troubleshooting and recovery

- **ImagePullBackOff:** verify the exact image tag is loaded into the existing Kind cluster (`kind load docker-image ... --name kind`) and that the chart uses `IfNotPresent`. Do not switch to `latest`.
- **Rollout timeout:** stop and collect `kubectl describe` for the affected workload/pods and bounded, redacted logs. Check events/probes and image availability. Do not repeatedly upgrade or restart unrelated services.
- **Gateway is ready but tools fail:** use `/upstreams/status`, verify internal Service DNS/ports and `tools/list`, then inspect only sanitized gateway logs. Do not expose upstream Secret data.
- **Store not ready:** do not delete either PVC or restart MariaDB as a guess. Check WordPress and MariaDB readiness, pod events, PVC Bound state, and verified backup availability. If a store image change is implicated, review the known-good Helm revision before rollback.
- **Helm reports failure while pods look ready:** inspect `helm status`, `helm history`, Deployment image/replicas, health endpoints, and logs. Treat Helm status and live health as separate facts. Do not roll back automatically; identify and review the exact target revision first.
- **Rollback also fails:** stop. Preserve the release, workloads, PVCs, and database; report current Helm revision, workload health, events, and a redacted manifest diff before taking further action.

## What was tested for this runbook

Local validation on 2026-10-22: `helm lint` and `helm template` succeeded for all eight repository charts. The store chart was linted/rendered with disposable placeholder passwords, and its output was suppressed; no live Secret value was read.

A live no-op lifecycle check was performed only for `mcphub-support-mcp` after its current effective non-secret values were inspected and its rendered manifest compared byte-for-byte with the deployed manifest. Commands:

```powershell
helm upgrade mcphub-support-mcp .\infra\helm\mcphub-support-mcp --namespace mcphub --reuse-values --wait --timeout 5m
helm rollback mcphub-support-mcp 3 --namespace mcphub --wait --timeout 5m
```

The upgrade created revision 4; rollback created deployed revision 5 pointing back to revision 3. The manifest remained equal to revision 3, rollout status succeeded, both replicas remained Ready on `mcphub-support-mcp:persistent-v2`, and the original pod names/restart counts were unchanged. This verifies the Helm command path for one stateless MCP release without a pod restart. It does not verify an image-changing upgrade, store/MariaDB procedure, or gateway procedure. Those require separate maintenance approval and risk review; in particular, a gateway image can run application startup schema initialization.
