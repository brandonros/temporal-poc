# temporal-poc

A small Temporal playground on k3s: durable workflows, worker autoscaling, and
compensation. Helmfile installs Temporal, Postgres, KEDA, and monitoring.

Two examples share the same Temporal server and `default` namespace:

- **Scaling:** an HTTP API starts a slow greeting workflow; KEDA scales its worker
  from zero as work arrives. This is the deployed example.
- **Saga:** a local script simulates an order and rolls back partial work. Use it
  to explore compensation without deploying another app.

Python lives in [apps/](apps/). Configuration lives in [values/](values/), with
chart versions pinned in [helmfile.yaml](helmfile.yaml).

## Start the stack

```bash
brew install lima helmfile
helmfile init
limactl start --name=k3s --memory=8 template://k3s
limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/config
helmfile apply
```

The first apply downloads charts and waits for Postgres before running Temporal's
schema job. `helmfile destroy` removes releases but retains CNPG PVCs and CRDs.
`limactl delete -f k3s` removes the VM and its data.

## Scaling

```bash
curl -X POST http://api.127.0.0.1.sslip.io/greet \
  -H 'Content-Type: application/json' -d '{"name":"brandon"}'
```

[api.py](apps/scaling/api.py) starts `GreetWorkflow` on the `scaling` task queue.
[worker.py](apps/scaling/worker.py) processes it with a five-second delay.

| Workload | KEDA trigger | Replicas |
|---|---|---|
| `scaling-api` | Traefik request rate | 1–5 |
| `scaling-worker` | Temporal task-queue backlog | 0–6 |

The API stays up to accept requests. A cold worker waits for KEDA polling and
Python dependency installation before processing the queued workflow.

The [app-template values](values/scaling/) are split into API and worker files.
Each includes its source ConfigMap and KEDA ScaledObject. `replicas: null` leaves
scaling to KEDA; source checksums trigger rollouts when Python files change.

## Saga

See [apps/saga/](apps/saga/README.md) for the runnable order example. It reserves
inventory, charges a payment, and creates a shipment. Inject a lost response to
watch the workflow compensate in reverse order.

## UIs

| UI | Address |
|---|---|
| Temporal | http://temporal.127.0.0.1.sslip.io |
| Grafana | http://grafana.127.0.0.1.sslip.io (`admin` / `prom-operator`) |
| Prometheus | http://prometheus.127.0.0.1.sslip.io |
| Alertmanager | http://alertmanager.127.0.0.1.sslip.io |

The `sslip.io` hosts resolve to loopback. This is a local PoC: Temporal has TLS
and authorization disabled, Grafana uses the chart's default password, and
Postgres has one instance with backups disabled.
