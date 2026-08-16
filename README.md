# k3s PoC stack

Prometheus + KEDA + CloudNativePG (Postgres 18) + Temporal, deployed declaratively
with [helmfile](https://helmfile.readthedocs.io/).

## Versions

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
  scaling-demo/                   # the four demo workloads + their ScaledObjects
    apps/                         # their Python source, mounted from ConfigMaps
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
→ Temporal → the demo chart.

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

## The demo apps

Two of the four Python apps in [charts/scaling-demo/apps/](charts/scaling-demo/apps/),
mounted into stock `python:3.12-slim` pods from ConfigMaps — no image build, no
registry, no CI. `pip install` runs at container start (~30-45s), the tradeoff.

- **`poc-worker`** — Temporal worker running `GreetWorkflow`. The activity sleeps
  5s with `max_concurrent_activities: 2`, so a burst builds a visible backlog.
- **`poc-api`** — aiohttp facade. `POST /greet` starts the workflow **by name**
  (so it shares no code with the worker), waits for the result, and returns it.

```bash
curl -X POST http://api.127.0.0.1.sslip.io/greet \
  -H 'Content-Type: application/json' -d '{"name":"brandon"}'
# {"message":"Hello, brandon!","workflow_id":"greet-...","seconds":5.11}
```

Editing a `.py` file re-renders the ConfigMap, and a `checksum/src` pod annotation
forces the rollout — a ConfigMap change alone would not restart the pods.

## Nexus

A second pair of apps demonstrating cross-namespace calls. Same ConfigMap
pattern, deliberately separate from the direct-call pair above.

- **`nexus-worker`** (handler) — Temporal namespace `poc`, task queue
  `poc-nexus-queue`. Serves `GreetService` via `nexus_service_handlers`, and
  self-registers the Nexus Endpoint on startup.
- **`nexus-api`** (caller) — Temporal namespace `caller`. One process running
  both an aiohttp server and a worker for `CallerWorkflow`, because Nexus
  operations are invoked from workflows, not plain clients.

```bash
curl -X POST http://nexus.127.0.0.1.sslip.io/greet \
  -H 'Content-Type: application/json' -d '{"name":"brandon"}'
# {"message":"Hello, brandon!","via":"nexus endpoint 'greet-endpoint'",
#  "mode":"workflow-backed (2 workflows)","caller_namespace":"caller",
#  "workflow_id":"caller-...","seconds":5.23}
```

The point: `nexus-api` references only the endpoint *name*. It never names
`poc` or `poc-nexus-queue` — contrast `poc-api`, which must pass
`task_queue="poc-task-queue"` on every call.

### Two handler styles on the same endpoint

`GreetService` exposes two operations with identical signatures. A caller cannot
tell from the contract which is which — that is the abstraction.

| Route | Handler | Workflows/req | Latency |
|---|---|---|---|
| `POST /greet` | `@workflow_run_operation` | **2.0** | ~5.2s |
| `POST /greet-sync` | `@sync_operation` | **1.0** | ~0.2s |

`sync_operation` handles the request inline and starts no workflow — no history,
nothing to replay. It must return inside a **10-second handler deadline**, which
is why it does no sleeping while `greet` runs a 5s activity. So the latency
column is *not* a like-for-like comparison; the useful number in it is that a
full Nexus round trip costs **~200ms** of overhead.

A `sync_operation` can also use a Temporal Client to Signal, Query, Update, or
Update-With-Start a workflow. Those stay reliable and synchronous, but they touch
a workflow, so they cost one execution again.

**Standalone Nexus Operations** — invoked straight from a Client with no caller
workflow at all — would drop this to 0 workflows for the sync case. That API is
pre-release and absent from temporalio 1.18.1 (`Client` has no nexus methods),
so it is not used here.

**Server config needed: none.** Nexus is on by default in server 1.31
(`system.enableNexus`), `httpPort: 7243` is already set, and the system callback
URL is the default.

**Registration is runtime state, not chart config.** The Temporal chart has no
Nexus support, so `nexus-worker` registers the endpoint itself via
`operator_service.create_nexus_endpoint`. Two things make that safe:

- `CreateNexusEndpoint` is **not** idempotent — it fails `ALREADY_EXISTS`. The
  handler lists by name first and also catches the conflict, since check-then-act
  can lose a race. Verified on a scale-out: one replica logged `registered`, the
  rest `already registered`, zero restarts.
- A pre-existing endpoint pointing at the wrong task queue routes into a black
  hole, so a target mismatch triggers `update_nexus_endpoint` rather than being
  silently accepted.

Nexus also has a plain HTTP surface on `:7243` for non-Temporal callers, at
`/nexus/endpoints/{endpoint-UUID}/services/{Service}/{Operation}` — note it takes
the endpoint **UUID**, not its name.

## The two KEDA triggers

**A ScaledObject is the only way a Deployment gets replicas in this chart.** No
Deployment sets `replicas` — if it did, every `helm upgrade` would reset the
count and fight the HPA. All four are rendered from one template,
`scalingdemo.scaledobject` in `templates/_helpers.tpl`, with exactly two trigger
shapes: handlers scale on task-queue backlog, HTTP services on ingress req/s.
One ScaledObject per workload, one trigger each — KEDA permits only one
ScaledObject per `scaleTargetRef`, and a second on the same Deployment fights
over the one HPA and errors out.

| Workload | Trigger | Signal | Range |
|---|---|---|---|
| `poc-worker` | `temporal` | task-queue backlog | 0–6 |
| `poc-api` | `prometheus` | Traefik ingress req/s | 1–5 |
| `nexus-worker` | `temporal` | task-queue backlog | 1–4 |
| `nexus-api` | `prometheus` | Traefik ingress req/s | 1–4 |

Only `poc-worker` can reach zero. `nexus-worker` cannot: `DescribeTaskQueue`
reports backlog for the `workflow` and `activity` task types only — there is no
`nexus` type to ask for, and KEDA's scaler wouldn't accept one. A Nexus request
hitting a zero-replica handler would create no visible backlog, never wake it,
and time out. Above 1 the trigger still works, because the operations start real
workflows on that queue. The HTTP services can't reach zero either, since they
have to be up to receive the request that creates the work.

**`temporal`** reads backlog straight from the frontend over gRPC and scales to
roughly `backlog / targetQueueSize`. Needs KEDA ≥ 2.17. Note the field is
`queueTypes` (plural, comma-separated) — `queueType` is silently ignored and
falls back to workflow-only. For Temporal Cloud, add a `TriggerAuthentication`
with an `apiKey` parameter.

**`prometheus`** queries `traefik_service_requests_total`, which k3s's Traefik
already emits (`--metrics.prometheus=true` on a `metrics` entrypoint) — it just
needed the PodMonitor in `values/kube-prometheus-stack.yaml`. **The API needs no
instrumentation to be autoscaled.** Traefik labels backends
`<namespace>-<service>-<port>@kubernetes`.

Verified end to end: a cold `POST /greet` against a zero-replica worker took 28s
(KEDA scale-up + `pip install`), then 5.1s warm. A 40-request burst drove the
worker 0→5→0 with 42/42 workflows completing, and 90s of sustained traffic drove
the API 1→5 at 2.6 req/s/replica.

## Before you call it production

- `grafana.adminPassword` is the chart default (`prom-operator`). Change it or
  point at an existing Secret.
- Temporal runs with TLS and authz off, which is fine inside the cluster but
  means anyone with pod network access can drive workflows.
- The demo apps ship their source in ConfigMaps and `pip install` at container
  start. That is fine for a PoC and wrong for production: build images, pin the
  dependencies in the image, and drop the `checksum/src` annotation.
- Memory requests are tight. The values here declare **~3.9Gi of requests with
  every workload at its minimum** (`poc-worker` at zero), before the chart
  defaults for prometheus-operator, node-exporter, kube-state-metrics and the
  KEDA webhooks, and before anything scales out. A 4Gi node does not fit this;
  give it 8Gi, or trim the Prometheus and Temporal history requests.
- On real hardware, raise `cluster.instances` to 3 and the per-service Temporal
  requests, and give Prometheus more than 20Gi.
- `backups.enabled: false`. Real backups need the `cnpg/plugin-barman-cloud`
  chart plus an object store.

## Verify

```bash
kubectl get cluster -n temporal                 # CNPG: expect "Cluster in healthy state"
kubectl get scaledobject,hpa -n temporal        # KEDA: READY/ACTIVE should be True
kubectl get ingress -A                          # the four UIs + the two demo APIs
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
