# temporal-poc

A small Temporal playground on k3s: durable workflows, worker autoscaling, and
compensation. Helmfile installs Temporal, Postgres, KEDA, and monitoring.

Two examples share the same Temporal server and `default` namespace:

- **Greeter:** an HTTP API starts a slow greeting workflow; KEDA scales its worker
  from zero as work arrives.
- **Saga:** an API starts a simulated order; its worker compensates partial work
  on failure. State lives in Postgres and survives worker restarts.

Each example has an API and worker under `apps/`, with matching app-template values:

```text
apps/greeter/      values/greeter/
apps/saga/         values/saga/
                   values/infra/
```

Infrastructure values are grouped under `values/infra/`. Chart versions are pinned
in [helmfile.yaml](helmfile.yaml). Both workers scale on their own task queues;
both APIs scale on Traefik request rate.

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

## Greeter

```bash
curl -X POST http://greeter.127.0.0.1.sslip.io/greet \
  -H 'Content-Type: application/json' -d '{"name":"brandon"}'
```

[api.py](apps/greeter/api.py) starts `GreetWorkflow` on the `greeter` task queue.
[worker.py](apps/greeter/worker.py) processes it with a five-second delay.

| Workload | KEDA trigger | Replicas |
|---|---|---|
| `greeter-api` | Traefik request rate | 1–5 |
| `greeter-worker` | Temporal task-queue backlog | 0–6 |

The API stays up to accept requests. A cold worker waits for KEDA polling and
Python dependency installation before processing the queued workflow.

The [app-template values](values/greeter/) are split into API and worker files.
Each includes its source ConfigMap and KEDA ScaledObject. `replicas: null` leaves
scaling to KEDA; source checksums trigger rollouts when Python files change.

## Saga

```bash
curl http://saga.127.0.0.1.sslip.io/orders \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"demo-001","shipping_fault":"lost_reply"}'
curl http://saga.127.0.0.1.sslip.io/orders/demo-001
```

POST returns immediately; GET reports progress and the final compensation state.
See [apps/saga/](apps/saga/README.md) for the failure modes and storage behavior.

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
