# k3s PoC stack

Prometheus + KEDA + CloudNativePG (Postgres 18) + Temporal, deployed declaratively
with [helmfile](https://helmfile.readthedocs.io/).

## Versions

All pinned versions were verified against the live chart repos.

| Component | Chart | Chart version | App version |
|---|---|---|---|
| Prometheus/Grafana/Alertmanager | `prometheus-community/kube-prometheus-stack` | `88.3.0` | `v0.93.0` |
| KEDA | `kedacore/keda` | `2.20.2` | `2.20.2` |
| CloudNativePG operator | `cnpg/cloudnative-pg` | `0.29.0` | `1.30.0` |
| Postgres cluster | `cnpg/cluster` | `0.8.1` | `18.4` |
| Temporal | `temporalio/temporal` | `1.6.0` | `1.31.2` |

Repository URLs:

```
prometheus-community  https://prometheus-community.github.io/helm-charts
kedacore              https://kedacore.github.io/charts
cnpg                  https://cloudnative-pg.github.io/charts
temporalio            https://go.temporal.io/helm-charts
```

## Layout

```
helmfile.yaml                     # the whole stack, with ordering via `needs:`
values/
  kube-prometheus-stack.yaml      # k3s-specific tuning
  keda.yaml
  cloudnative-pg.yaml             # the operator
  postgres-cluster.yaml           # the Postgres 18 cluster itself
  temporal.yaml                   # points Temporal at the CNPG cluster
charts/
  scaling-demo/                   # demo worker + KEDA ScaledObject
```

The operator and the cluster are two separate charts on purpose: CloudNativePG
splits the control plane (`cloudnative-pg`, cluster-scoped, installs the CRDs)
from the workload (`cluster`, one release per database cluster). You install the
first once and the second once per cluster.

## Prerequisites

```bash
brew install helmfile          # or: https://github.com/helmfile/helmfile/releases
helmfile init                  # installs the helm-diff plugin helmfile needs
```

## Deploy

```bash
helmfile deps      # resolve chart versions
helmfile diff      # preview changes
helmfile apply     # converge
```

Order enforced by `needs:` — monitoring → KEDA / CNPG operator → Postgres cluster
→ Temporal → ScaledObjects.

Tear down with `helmfile destroy`. Note that CNPG PVCs and the CRDs
(`helm.sh/resource-policy: keep`) survive on purpose; delete them by hand if you
want a truly clean slate.

## Decisions worth knowing

**Every release sets `disableValidationOnInstall: true`.** `helmfile apply`
diffs all releases up front, before `needs:` installs anything, so on a first
apply the CRDs for `ServiceMonitor` / `ScaledObject` / `Cluster` don't exist yet
and the diff aborts the entire run. It only relaxes the diff preview — the
install itself still validates.

**The kubelet resource endpoint needed repointing.** The chart still defaults
`kubelet.serviceMonitor.resourcePath` to `/metrics/resource/v1alpha1`, which
modern kubelets removed; it 404s on k3s 1.36. Set to `/metrics/resource`.

**k3s breaks the default Prometheus scrape targets.** k3s runs the apiserver,
controller-manager and scheduler in one process and doesn't expose them on the
ports the chart's ServiceMonitors expect. Left enabled they sit permanently down
and fire `…Down` alerts, so `kubeControllerManager`, `kubeScheduler`, `kubeProxy`
and `kubeEtcd` are disabled along with their matching alert rule groups. `kubelet`
still provides cAdvisor, probe and resource metrics.

**ServiceMonitor discovery had to be opened up.** kube-prometheus-stack defaults
`serviceMonitorSelectorNilUsesHelmValues: true`, which limits Prometheus to
ServiceMonitors labelled `release: kube-prometheus-stack`. Temporal's, KEDA's and
CNPG's monitors aren't, so those selectors are set to `false`.

**Temporal chart 1.x has no bundled subcharts.** Unlike the old `0.x` charts,
there is no Cassandra, Elasticsearch, Prometheus or Grafana dependency to turn
off — you only supply an external datastore. `shims.dockerize` and
`shims.elasticsearchTool` are set to `false` because those exist for Temporal
1.29 images and the server here is 1.31.2.

**Temporal needs two databases.** `cluster.initdb` creates `temporal`; the
`databases:` list renders a CNPG `Database` CR (available since operator 1.25)
for `temporal_visibility`. Because CNPG owns database creation, each Temporal
datastore sets `createDatabase: false` and keeps `manageSchema: true` so the
chart's hook job still builds and upgrades the tables. The `postgres12_pgx`
plugin is correct for Postgres 18 — the `v12` schema directory covers 12+.

**`fullnameOverride: temporal-pg` is load-bearing.** The `cnpg/cluster` chart
appends `-cluster` to the release name by default, which would rename the
generated Service and Secret out from under `values/temporal.yaml`. The override
pins them to `temporal-pg-rw` and `temporal-pg-app`.

**The image tag is pinned past the chart's default.** `version.postgresql: "18"`
alone yields the floating tag `postgresql:18`; `cluster.imageName` pins
`18.4-system-trixie` so a `helmfile apply` can't move the Postgres binary
underneath an existing data directory.

**Credentials are never written down.** CNPG generates the `temporal-pg-app`
Secret for the `temporal` role; Temporal consumes it via `existingSecret` /
`secretKey: password`, and the chart injects it as an env var rather than baking
it into the ConfigMap.

**`numHistoryShards: 512` is immutable.** It cannot be changed after the first
deploy without starting from a fresh cluster. Decide now, not later.

**One ScaledObject per target.** KEDA permits only one ScaledObject per
`scaleTargetRef` — two objects on the same Deployment fight over one HPA and the
second errors out. Both triggers therefore live in a single ScaledObject, and
KEDA scales to the highest count any trigger requests.

## The two KEDA triggers

Both are in [charts/scaling-demo/templates/scaledobject.yaml](charts/scaling-demo/templates/scaledobject.yaml).

1. **`temporal`** — reads the task-queue backlog directly from the Temporal
   frontend at `temporal-frontend.temporal.svc.cluster.local:7233`. Scales to
   roughly `backlog / targetQueueSize`. Needs KEDA ≥ 2.17. For Temporal Cloud,
   add a `TriggerAuthentication` exposing an `apiKey` parameter.
2. **`prometheus`** — a PromQL query against
   `kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090`, watching
   p95 activity schedule-to-start latency. That series exists only because
   `server.metrics.serviceMonitor.enabled: true` is set in the Temporal values.

`minReplicaCount: 0` means scale-to-zero is on; `activationThreshold` /
`activationTargetQueueSize` control the wake-from-zero decision separately from
the scaling threshold.

## Before you call it production

- `grafana.adminPassword` is the chart default (`prom-operator`). Change it or
  point at an existing Secret.
- Temporal runs with TLS and authz off, which is fine inside the cluster but
  means anyone with pod network access can drive workflows.
- The demo worker in `charts/scaling-demo` is an `alpine` container that sleeps.
  Swap in a real worker image and set `worker.command`.
- Everything is sized for a **single 3.8Gi / 4 CPU node** (~2.0Gi of memory
  requests, leaving ~1.9Gi headroom for KEDA to scale into). On real hardware,
  raise `cluster.instances` to 3, Prometheus retention back to 15d, and the
  per-service Temporal requests.
- `backups.enabled: false`. Real backups need the `cnpg/plugin-barman-cloud`
  chart plus an object store.

## Verify

```bash
kubectl get cluster -n temporal                 # CNPG: expect "Cluster in healthy state"
kubectl get scaledobject,hpa -n temporal        # KEDA: READY/ACTIVE should be True
kubectl get ingress -A                          # the four UIs
```

## UIs

Exposed through the Traefik that k3s already ships, on `sslip.io` wildcard DNS —
`*.127.0.0.1.sslip.io` resolves to `127.0.0.1` from public DNS, so there is no
`/etc/hosts` entry to maintain and no path-prefix rewriting to configure.

| | |
|---|---|
| Grafana | http://grafana.127.0.0.1.sslip.io (admin / `prom-operator`) |
| Prometheus | http://prometheus.127.0.0.1.sslip.io |
| Alertmanager | http://alertmanager.127.0.0.1.sslip.io |
| Temporal | http://temporal.127.0.0.1.sslip.io |

Lima binds forwarded ports to `127.0.0.1`, so these reach your machine only, not
the LAN. If you ever change Lima to bind `0.0.0.0`, change `grafana.adminPassword`
in the same commit — and note Grafana persists the password to its PVC on first
boot, so a later values change won't take effect on an existing install.

Offline (on a plane, no DNS) the sslip.io lookup fails; port-forward still works:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
kubectl -n temporal   port-forward svc/temporal-web 8080:8080
```
