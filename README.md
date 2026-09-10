# temporal-poc

A playground for operating Temporal on k3s: durable workflows, autoscaling, and
compensation. Helmfile installs the ingress, storage, observability, and application stack.

- **Greeter:** an API starts a five-second workflow; KEDA scales its worker from zero.
- **Saga:** an API starts a simulated order; its worker compensates partial failures.
  Operation records persist in Postgres. See [Saga details](apps/saga/README.md).

Both have an API and worker, separate task queues, and the `default` Temporal namespace.
Python lives in `apps/{greeter,saga}/`. Releases and versions are in `helmfile.yaml`.

| Layer | Configuration | Components |
|---|---|---|
| Kubernetes | `lima/k3s.yaml` | k3s, ServiceLB, local-path storage; bundled Traefik disabled |
| Infrastructure | `values/infra/` | Traefik, Zot, SeaweedFS, Prometheus/Grafana, Loki, Tempo, Alloy, KEDA, cert-manager, CNPG operator, Barman plugin |
| Platform | `values/platform/` | PostgreSQL with daily backups and WAL archiving; Temporal |
| Applications | `values/{greeter,saga}/` | API and worker configuration |

Alloy sends pod logs to Loki and OTLP traces to Tempo; both are provisioned in Grafana.
Traefik exports ingress traces. Python workflow tracing needs application instrumentation.
SeaweedFS holds blobs, logs, traces, and database backups in separate buckets.

## Run

```bash
brew install lima helmfile just
just init
just apply
just greet
just order demo-001 lost_reply
just order-status demo-001
```

`just` lists the commands. `just init` prepares a 12 GiB VM and writes a repo-local
`.kubeconfig`; recipes use it without changing your global Kubernetes config.
The Lima YAML applies to newly created VMs; existing VMs need bundled Traefik disabled
before Helmfile can own it. Use a new order ID each time. Cold workers wait for
KEDA polling and image pulls.

`lima/registries.yaml` configures local registry pulls; `just registry-config`
updates an existing VM and restarts k3s. Zot is exposed at
`registry.127.0.0.1.sslip.io` over HTTP for this local lab.

`just apply` installs infrastructure, builds and pushes source-tagged images with Lima
BuildKit, then deploys the apps. `just infra`, `just images`, and `just apps` run each stage.

`just diff` previews changes; `just status` shows pods and scaling resources.
`just destroy` removes releases but retains database volumes and CRDs.
`limactl delete -f k3s` removes the VM and its data.

## Demos

After `just apply`, run any of these with Python 3 available:

```bash
just demo-load                 # 30 greetings, worker counts and latency
just demo-cold                 # wait for zero workers, then send the burst
just demo-order                # successful order and persistent records
just demo-rollback             # lost shipping response, retries and compensation
just demo-restart              # roll workers after shipping commits, verify cleanup
```

The scripts use unique order IDs, bounded requests and polling deadlines, and exit
nonzero on failure. Saga demos read Postgres through the worker's credentials and
check duplicate-order rejection. `demo-restart` rolls all Saga worker replicas;
run it without other Saga traffic. Cold-start waiting allows six minutes for KEDA
and the Saga demos allow five minutes for completion. No demo resets the database
or changes KEDA settings. These are live walkthroughs, not a test suite.

## UIs

| UI | Address |
|---|---|
| Temporal | http://temporal.127.0.0.1.sslip.io |
| Grafana | http://grafana.127.0.0.1.sslip.io (`admin` / `prom-operator`) |
| Prometheus | http://prometheus.127.0.0.1.sslip.io |
| Alertmanager | http://alertmanager.127.0.0.1.sslip.io |

The hosts resolve to loopback. Temporal has TLS and authorization disabled,
Grafana and S3 use local demo credentials. Postgres and its SeaweedFS backups share
the same VM; deleting it removes both.
