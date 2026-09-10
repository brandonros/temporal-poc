# temporal-poc

Temporal on Lima/k3s with two Python apps: **Greeter** demonstrates KEDA scaling
from zero; **[Saga](apps/saga/README.md)** demonstrates retries and compensation
with operation records in Postgres.

## Run

Requires Python 3 and these tools:

```bash
brew install lima helmfile just kubectl
just init
just apply
```

`init` creates a 12 GiB VM and writes `.kubeconfig`. `apply` installs dependencies,
builds and pushes app images to Zot, then deploys the apps.

| Configuration | Contents |
|---|---|
| `lima/` | k3s, ServiceLB, local-path storage, registry settings; bundled Traefik disabled |
| [`values/infra/`](values/infra/README.md) | Shared infrastructure, grouped by responsibility; see the component guide |
| `values/platform/` | Postgres with daily backups and WAL archiving; Temporal |
| `values/{greeter,saga}/` | APIs and workers |

Chart versions and dependencies are in `helmfile.yaml`. Alloy ships pod logs to
Loki and traces to Tempo. Traefik emits ingress traces; Python workflow tracing
still needs instrumentation.

## Demos

```bash
just demo load --requests 30   # greetings, worker counts, latency
just demo load --cold          # wait for zero workers first
just demo order                # successful order
just demo rollback             # lost shipping response and compensation
just demo restart              # restart workers after shipping commits
```

`scripts/demo.py` runs these live checks, verifies Saga database records and
rejection of duplicate orders, and exits nonzero on failure. `restart` rolls all
Saga workers; run it without other Saga traffic.

`scripts/images.py` builds app images inside Lima and pushes them to
`registry.127.0.0.1.sslip.io`. Tags hash the Dockerfile, requirements, and Python
source; Helm uses the same script to select each image.

## UIs

| UI | Address |
|---|---|
| Temporal | http://temporal.127.0.0.1.sslip.io |
| Grafana | http://grafana.127.0.0.1.sslip.io (`admin` / `prom-operator`) |
| Prometheus | http://prometheus.127.0.0.1.sslip.io |
| Alertmanager | http://alertmanager.127.0.0.1.sslip.io |

`just status` shows pods and scaling; `just diff` previews Helm changes.
`just destroy` removes releases but retains volumes and CRDs.
`limactl delete -f k3s` deletes the VM, including databases and backups.

Local lab defaults: Temporal has no TLS or authorization, Zot uses HTTP,
and Grafana/S3 use demo credentials.
