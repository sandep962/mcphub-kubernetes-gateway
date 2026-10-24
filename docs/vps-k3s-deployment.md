# Production-like deployment to a Linux VPS with k3s

This guide reuses the existing Helm charts for a **new target K3s VPS**. It is not the runbook for the existing Azure demo: Azure was deployed and live-checked through a separate reviewed workflow, including HTTPS Ingress for its configured hostname. No second VPS, DNS zone, firewall, registry, or target cluster was contacted or changed for this guide. The files under `infra/helm/values/vps-k3s/` are separate from Kind defaults and intentionally contain `REPLACE_WITH_...` placeholders. Replace every placeholder and review the rendered output before using them. A new target still needs its own DNS, certificate, and TLS configuration.

## Readiness and blockers

The charts already keep application Services internal (`ClusterIP`), define readiness/liveness probes, resource requests/limits, and NetworkPolicies. The gateway uses in-cluster DNS for WooCommerce, Inventory, Support, and MariaDB. Store data is persisted by a WordPress PVC and a MariaDB StatefulSet claim. The Ingress chart already routes the storefront and dashboard.

Production-specific gaps addressed here:

- Images in Kind are local tags and are not available to a VPS. Production values use immutable-registry placeholders and optional Kubernetes `imagePullSecrets`.
- The store chart previously required database passwords as Helm values and rendered a Kubernetes Secret from them. It now supports `mariadb.existingSecret` so a secret created outside Helm is referenced without putting passwords in Helm release values. With the local default `existingSecret: ""`, Kind behavior is retained.
- The Ingress chart now accepts an optional path and per-route TLS Secret. Existing local route values still render `/` and no TLS. VPS values disable the local hostless catch-all and add a gateway route.
- K3s local-path volumes are tied to the node hosting the volume. They survive pod recreation but are not a backup and do not protect against VPS/disk loss. Use encrypted off-host backups; consider remote/replicated CSI storage if node-loss recovery is required. [K3s storage documentation](https://docs.k3s.io/add-ons/storage)

The current worktree includes OAuth handlers and schema migrations through version 5. Normal repository connections auto-migrate by default unless `MCPHUB_AUTO_MIGRATE` is false; the demo/VPS application values set it false. The metadata chart's versioned migration Job is opt-in and is the controlled path for a schema change. The OAuth routes remain in the image; blank issuer/resource values do not remove those routes, and OAuth endpoint requests fail closed until valid HTTPS URLs are configured. Build from a reviewed source revision containing the required gateway features and explicit-migration control; do not select an older image merely to avoid the migration.

The migration Job invokes the schema initializer under a MariaDB advisory lock and records versions 1–5. Version 4 creates the OAuth tables. Version 5 supports databases created by the earlier consent schema by adding/backfilling the SHA-256 `resource_hash` and replacing the oversized resource-string primary key while preserving consent rows; fresh databases are created with the hash column and compact primary key directly. The initializer also uses `INSERT IGNORE` for default upstreams, demo Inventory rows, and Support tickets; it does not update or delete existing records, but a fresh database gains those seed records. MariaDB DDL can auto-commit, so inspect state after a failed migration and do not retry blindly. Review the exact migration and verify a backup before approving it. Image builds do not run migrations; demo/VPS app startup does not auto-migrate because those values set `MCPHUB_AUTO_MIGRATE=false`.

Additional prerequisites that cannot be filled in without owner input: VPS provider/IP, supported Linux release and capacity, real DNS names, image registry and access policy, TLS certificate workflow, store canonical URL, storage sizing, backup destination/retention, and an approved gateway/provisioner image compatible with the selected source revision and schema. No domain or credential is assumed here.

## Chart and environment assumptions

| Release | Existing chart | Release namespace | Workload namespace | VPS-specific notes |
|---|---|---|---|---|
| `mcphub-store` | `infra/helm/mcphub-store` | `default` | `mcphub` | Set `storageClassName` to the selected VPS storage class; production file uses K3s `local-path` for single-node use. Use pre-created `mcphub-db-secret`. |
| `mcphub-metadata` | `infra/helm/mcphub-metadata` | `mcphub` | `mcphub` | Provisioner image must be approved; the chart runs a pre-install/pre-upgrade Job. It is not an OAuth migration command. Do not upgrade it casually. |
| `mcphub-woocommerce-mcp` | `infra/helm/mcphub-woocommerce-mcp` | `mcphub` | `mcphub` | Uses pre-created `mcphub-woocommerce-api`; production canonical WordPress URL must be the real HTTPS URL WooCommerce expects. |
| `mcphub-inventory-mcp` | `infra/helm/mcphub-inventory-mcp` | `mcphub` | `mcphub` | Database credentials come from `mcphub-metadata-secret`; no external Service. |
| `mcphub-support-mcp` | `infra/helm/mcphub-support-mcp` | `mcphub` | `mcphub` | Database credentials come from `mcphub-metadata-secret`; no external Service. |
| `mcphub-gateway` | `infra/helm/mcphub-gateway` | `mcphub` | `mcphub` | Internal upstream URLs, 30-second tool timeout, LLM Secret reference, ClusterIP Service. OAuth URLs remain blank. |
| `mcphub-dashboard` | `infra/helm/mcphub-dashboard` | `mcphub` | `mcphub` | ClusterIP only; route through TLS Ingress. |
| `mcphub-ingress` | `infra/helm/mcphub-ingress` | `mcphub` | `mcphub` | Use the actual class provided by the selected cluster. TLS Secret must already exist in `mcphub`. |

The local Kind Ingress uses a project-specific class and local HTTP hosts. Do not carry that class, the local catch-all route, localhost URLs, or Kind port-forward assumptions to the VPS. K3s commonly deploys Traefik and its ServiceLB by default, but verify the actual VPS configuration instead of assuming it is enabled; K3s documents these packaged components and their networking behavior [here](https://docs.k3s.io/networking/networking-services). The K3s network policy controller is also configurable; confirm it has not been disabled before relying on these policies ([K3s Network Policy Controller](https://docs.k3s.io/networking/networking-services)).

## VPS, DNS, firewall, and TLS prerequisites

1. Provision a supported Linux VPS with enough memory, CPU, and disk for the application plus K3s, image cache, logs, database growth, and backups. Current chart steady-state requests sum to approximately **1.1 CPU and 1,600Mi memory**; configured limits sum to approximately **5.5 CPU and 5.25Gi memory**. These figures include the chart replica counts, but exclude K3s/system overhead and actual utilization; they are a chart-derived estimate, not a sizing recommendation or load test. The transient metadata provisioning Job adds 50m CPU/64Mi request and 250m/256Mi limit. The two store claims default to 2Gi each and must be sized from actual data and growth before install.
2. Choose an explicit K3s version and installation configuration. For a new single-server cluster, enable K3s Secret encryption before the first start, protect `/etc/rancher/k3s/config.yaml` and the generated encryption key, and restrict kubeconfig access. K3s documents that enabling encryption has restart/key-management implications; do not toggle it casually on an existing cluster. See [K3s Secrets Encryption](https://docs.k3s.io/security/secrets-encryption).
3. Confirm K3s storage class, ingress controller/class, NetworkPolicy enforcement, node architecture, and `kubectl`/Helm connectivity. `local-path` is the usual K3s local provisioner class but permanently binds a volume to the node containing its data. If this is not a single-node VPS or local storage is unacceptable, choose and test a supported CSI class before using the values file.
4. Select three real DNS names for storefront, dashboard, and MCP gateway. Point them to the actual public ingress address only after the provider/IP is known. Do not publish MariaDB, upstream MCP Services, or Kubernetes API access to the public Internet.
5. Configure the provider firewall so only required web traffic reaches the ingress (normally TCP 80/443). Restrict SSH to administrator source addresses. Keep the Kubernetes API and node/cluster ports private or allowlisted according to the chosen topology. Do not expose NodePorts or ClusterIP services.
6. Obtain a valid TLS certificate covering all three hostnames and create the referenced TLS Secret in `mcphub` using a protected certificate workflow. No cert-manager chart exists here; adding one is optional and is not required if certificate lifecycle is managed externally. Do not place private-key material in Helm values or source control.
7. Determine whether the K3s bundled Traefik ServiceLB or another ingress controller is the owner of ports 80/443. If K3s Traefik is disabled, install/operate an approved controller separately. Check `kubectl get ingressclass` and use that exact class in the production values.

## Image build and delivery

The Dockerfiles expect the repository root as build context. Current examples use local tags; those tags must not be assumed to exist on the VPS. Build in a trusted CI/release environment from a reviewed source revision, assign unique immutable tags (prefer image digests), scan and retain artifacts, and push to an approved private registry. Do not pass registry credentials as command-line arguments; use the CI credential store or an interactive credential helper.

From the repository root, with real registry host and immutable release tag supplied by the owner/CI:

```powershell
$registry = '<approved-registry-host>/<approved-project>'
$tag = '<immutable-release-tag>'
docker build -f apps/store/Dockerfile -t "$registry/mcphub-wordpress`:$tag" .
docker build -f apps/woocommerce-mcp/Dockerfile -t "$registry/mcphub-woocommerce-mcp`:$tag" .
docker build -f apps/inventory-mcp/Dockerfile -t "$registry/mcphub-inventory-mcp`:$tag" .
docker build -f apps/support-mcp/Dockerfile -t "$registry/mcphub-support-mcp`:$tag" .
docker build -f apps/dashboard/Dockerfile -t "$registry/mcphub-dashboard`:$tag" .
# Build the gateway only from the approved non-OAuth source revision described above.
docker build -f apps/gateway/Dockerfile -t "$registry/mcphub-gateway`:$tag" .
```

Push each reviewed image through the approved registry workflow. The MariaDB upstream image also needs a reviewed version/digest; do not use an unpinned `latest` tag. The WordPress Dockerfile currently downloads `woocommerce.latest-stable` at build time, so reproducible production releases require pinning/reviewing that plugin version or retaining and promoting a tested immutable built image. Build/test architecture must match the VPS node architecture (for example, amd64 vs arm64).

Each workload chart now accepts `imagePullSecrets` and `imagePullPolicy`; defaults preserve local Kind behavior (`IfNotPresent`, no pull Secret). The example VPS files name `mcphub-registry-pull`. Create that Kubernetes Secret using a protected secret manager/CI workflow in namespace `mcphub`. If the registry is public or node-level containerd auth is intentionally managed, remove that reference from the values files rather than creating an unnecessary credential. Do not use `kubectl create secret ... --docker-password=...` with a literal password in shell history/process arguments.

## Secret preparation

Create these namespace-scoped Secrets before installing the workloads. Use a managed secret operator or protected administrative process; this repository does not include an external-secrets integration. The names and key names below are references, not secret values:

- `mcphub/mcphub-db-secret`: `database`, `username`, `password`, `root-password`. The store chart production values set `mariadb.existingSecret` and therefore do not render a Secret resource from Helm.
- `mcphub/mcphub-metadata-secret`: `database`, `username`, `password` for the separate application metadata database/account.
- `mcphub/mcphub-woocommerce-api`: `base-url`, `consumer-key`, `consumer-secret`. `base-url` must resolve to the internal WordPress Service from the WooCommerce pod; it is not the public canonical URL.
- `mcphub/mcphub-llm-secret`: `api-key`, if the Gemini-backed assistant is required.
- `mcphub/mcphub-registry-pull`: registry credentials if a private registry is used.
- `mcphub/mcphub-public-tls`: `tls.crt` and `tls.key` from the approved certificate workflow, with SANs matching the three real hostnames.

K3s Secret encryption must be enabled/verified; apply least-privilege Kubernetes RBAC and restrict shell access, backups, and CI logs. Never store Secret values in the production values directory or Helm release values. API-key bootstrap and user key issuance remain separate operational steps; issue and protect credentials only after validating the deployment and intended roles.

## Production values files

The example files are:

- `infra/helm/values/vps-k3s/mcphub-store.yaml`
- `infra/helm/values/vps-k3s/mcphub-metadata.yaml`
- `infra/helm/values/vps-k3s/mcphub-woocommerce-mcp.yaml`
- `infra/helm/values/vps-k3s/mcphub-inventory-mcp.yaml`
- `infra/helm/values/vps-k3s/mcphub-support-mcp.yaml`
- `infra/helm/values/vps-k3s/mcphub-gateway.yaml`
- `infra/helm/values/vps-k3s/mcphub-dashboard.yaml`
- `infra/helm/values/vps-k3s/mcphub-ingress.yaml`

All image, host, ingress-class, and registry names are conspicuous placeholders. Review resource sizing and storage capacities against the actual VPS. The gateway values keep OAuth issuer/resource blank. Gateway, Inventory, and Support use `database.autoMigrate: false` in production; local chart defaults remain `true` for compatibility. The Gemini egress rules are existing fixed `/32` CIDRs, not a stable domain-based allowlist; verify current provider routing from the VPS and change egress only through a separately reviewed narrow policy. Do not replace them with unrestricted `0.0.0.0/0` egress.

The store production values are for a fresh VPS release. Switching an existing Helm-managed store Secret to `existingSecret` changes the rendered manifest so Helm may remove the Secret resource it previously managed. Do not apply the production store file to the current Kind release or migrate an existing store release this way without a separate reviewed Secret ownership handoff, verified backup, and explicit maintenance plan.

## Install sequence and exact Helm commands

Use an administrator kubeconfig with restrictive filesystem permissions. Commands below are templates and cannot succeed until all placeholders and prerequisites are satisfied. Run them from the repository root using the reviewed chart/value versions. `--wait` is used; do not use `--atomic` for the store because rollback cannot undo database changes.

```bash
kubectl config current-context
kubectl create namespace mcphub
kubectl get storageclass
kubectl get ingressclass
```

Create the Secrets above through the protected workflow, then confirm only their names/keys exist without printing data. Check that the chosen images are available in the approved registry and that pull credentials are configured.

```bash
# Store release is in default; its values render workloads in mcphub.
helm upgrade --install mcphub-store ./infra/helm/mcphub-store \
  --namespace default --values ./infra/helm/values/vps-k3s/mcphub-store.yaml \
  --wait --timeout 10m

# Wait for the initial database and WordPress before metadata provisioning.
kubectl rollout status statefulset/mcphub-mariadb -n mcphub --timeout=10m
kubectl rollout status deployment/mcphub-wordpress -n mcphub --timeout=10m

# First-time metadata database/account provisioning only. The Job has pre-install/pre-upgrade hooks.
# Confirm the selected image is the reviewed metadata provisioner artifact.
helm upgrade --install mcphub-metadata ./infra/helm/mcphub-metadata \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-metadata.yaml \
  --wait --timeout 5m

# Explicit schema step. Run only after reviewing the migration and a verified metadata DB backup.
# The metadata chart first runs its existing DB-account provisioner hook; the opt-in Job then
# executes apps.shared.migrate_database from the reviewed gateway/provisioner image.
# The chart Job version labels the reviewed schema level; runId gives the immutable Job a unique name.
# Pick a fresh lowercase DNS-safe suffix (at most 22 characters) for this target cluster.
MIGRATION_RUN_ID='reviewed-20261024-1'
helm upgrade mcphub-metadata ./infra/helm/mcphub-metadata \
  --namespace mcphub --reuse-values \
  --set schemaMigration.enabled=true --set schemaMigration.version=5 \
  --set-string schemaMigration.runId="$MIGRATION_RUN_ID" \
  --wait --wait-for-jobs --timeout 10m
kubectl logs -n mcphub "job/mcphub-schema-migration-v5-$MIGRATION_RUN_ID"
kubectl get job "mcphub-schema-migration-v5-$MIGRATION_RUN_ID" -n mcphub

# The production app values keep automatic schema changes disabled. Install app releases only
# after the explicitly enabled migration Job completed successfully.

helm upgrade --install mcphub-woocommerce-mcp ./infra/helm/mcphub-woocommerce-mcp \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-woocommerce-mcp.yaml \
  --wait --timeout 5m
helm upgrade --install mcphub-inventory-mcp ./infra/helm/mcphub-inventory-mcp \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-inventory-mcp.yaml \
  --wait --timeout 5m
helm upgrade --install mcphub-support-mcp ./infra/helm/mcphub-support-mcp \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-support-mcp.yaml \
  --wait --timeout 5m

# Use a reviewed gateway image matching this source and schema. These values leave OAuth
# issuer/resource unset; configure OAuth only after HTTPS routing and schema readiness are verified.
helm upgrade --install mcphub-gateway ./infra/helm/mcphub-gateway \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-gateway.yaml \
  --wait --timeout 5m
helm upgrade --install mcphub-dashboard ./infra/helm/mcphub-dashboard \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-dashboard.yaml \
  --wait --timeout 5m

# Apply Ingress last, after the controller, Services, TLS Secret, DNS, and class are ready.
helm upgrade --install mcphub-ingress ./infra/helm/mcphub-ingress \
  --namespace mcphub --values ./infra/helm/values/vps-k3s/mcphub-ingress.yaml \
  --wait --timeout 5m
```

For an existing VPS release, inspect `helm history`, inspect only allowlisted deployed values, and compare the rendered manifest before each upgrade. Use `--reuse-values` only after reviewing how it merges with the production file; do not carry local Kind values into production. Do not rerun the metadata release casually because its pre-upgrade hook executes the provisioner Job. Do not change or resize a bound PVC through an ordinary app upgrade without a storage-specific plan and backup.

## Verification

```bash
helm list -A
kubectl get deployments,statefulsets,pods,pvc -n mcphub -o wide
kubectl get ingress -n mcphub
kubectl get ingressclass
kubectl rollout status statefulset/mcphub-mariadb -n mcphub --timeout=10m
kubectl rollout status deployment/mcphub-wordpress -n mcphub --timeout=10m
kubectl rollout status deployment/mcphub-woocommerce-mcp -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-inventory-mcp -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-support-mcp -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-gateway -n mcphub --timeout=5m
kubectl rollout status deployment/mcphub-dashboard -n mcphub --timeout=5m
kubectl get pvc -n mcphub
```

Use `curl --fail --show-error https://<actual-host>/` for storefront/dashboard and `/health`, `/ready` for gateway checks using the actual TLS host. Verify certificate chain and hostname. Authenticate to gateway with an approved API key through a protected client and call `GET /upstreams/status`, `GET /tools`, and a harmless read-only Inventory tool through the gateway. Confirm WooCommerce and Support tools are discovered; upstream MCP Services must remain ClusterIP and must not be given public Ingress routes. Confirm private data is not printed in terminal logs or shell traces.

Verify the store with read-only product/order checks and confirm both PVCs remain Bound. Confirm MariaDB and WordPress readiness, current image digests, expected replicas, and no unexplained restarts. Review sanitized events/logs. Validate backup restore in an isolated environment before declaring disaster recovery tested.

## Backup, recovery, upgrade, and rollback

K3s `local-path` data resides on the VPS node. If the node or disk is lost, the PVC alone does not provide off-node recovery. Back up **both** databases (WordPress/WooCommerce and MCPHub metadata) and the WordPress files PVC, encrypt backups, copy them off the VPS, apply retention, and regularly perform a restore drill to separate storage. Also back up the K3s datastore and its server token/encryption key according to K3s guidance. K3s snapshots are not a substitute for application-consistent MariaDB backups; protect snapshot/bootstrap encryption material separately. The `k3s etcd-snapshot` command applies to etcd datastore setups; single-server K3s may use SQLite by default, so select the correct K3s datastore backup method for the chosen topology. See [K3s storage](https://docs.k3s.io/add-ons/storage), [Secret encryption](https://docs.k3s.io/security/secrets-encryption), and [snapshot guidance](https://docs.k3s.io/cli/etcd-snapshot).

Before upgrades, take and verify database and file backups, record `helm history` and current images, render the candidate with the production values, inspect the manifest diff, and update one release at a time. Start with stateless MCP services, then dashboard/gateway, and store only during an approved maintenance window. Keep the previous immutable image and a known-good Helm revision. Wait and verify after each release before continuing. MariaDB version changes require their own compatibility/backup/restore plan.

Rollback exact release commands (substitute a previously reviewed revision):

```bash
helm history mcphub-store -n default
helm history mcphub-metadata -n mcphub
helm history mcphub-woocommerce-mcp -n mcphub
helm history mcphub-inventory-mcp -n mcphub
helm history mcphub-support-mcp -n mcphub
helm history mcphub-gateway -n mcphub
helm history mcphub-dashboard -n mcphub
helm history mcphub-ingress -n mcphub

helm rollback mcphub-store <known-good-revision> -n default --wait --timeout 10m
helm rollback mcphub-woocommerce-mcp <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-inventory-mcp <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-support-mcp <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-gateway <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-dashboard <known-good-revision> -n mcphub --wait --timeout 5m
helm rollback mcphub-ingress <known-good-revision> -n mcphub --wait --timeout 5m
```

Do not roll back a database schema/data change solely by rolling back Helm; coordinate a compatible application version or restore the verified backup under a separately approved recovery plan. A code rollback does not remove schema versions 4 or 5. Do not rerun a schema migration by deleting its Job or by changing its version casually; inspect the database state, preserved Job/logs, and verified backup first. Do not `helm uninstall`, delete PVCs, or remove the K3s datastore as an upgrade recovery measure. The metadata chart provisioning hook continues to run on metadata release upgrades and requires review before using that release.

## Upgrade and rollback test status

Helm lint and local template rendering are validation only. A previous runbook check performed a no-op upgrade/rollback of the Support MCP release in Kind. The existing Azure demo was deployed and live-verified through a separate workflow; the fresh-target sequence in this guide has not been run as written. The production values and optional chart features must still be validated against the target K3s version, ingress class, registry, storage class, DNS, TLS, and images before applying them to another VPS.
