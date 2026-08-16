# temporal-poc

Prometheus + KEDA + CloudNativePG (Postgres 18) + Temporal on a single-node k3s
VM, deployed with [helmfile](https://helmfile.readthedocs.io/).

Chart versions are pinned in [helmfile.yaml](helmfile.yaml). The settings that
aren't obvious are commented where they live, in [values/](values/).

## Run it

```bash
brew install lima helmfile
helmfile init                  # installs the helm-diff plugin apply depends on

limactl start --name=k3s --memory=8 template://k3s
limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/config

helmfile apply
```

`--memory=8` because the stack requests ~4.2Gi; Lima's 4GiB default won't
schedule it. The second line **overwrites `~/.kube/config`** — redirect it
elsewhere and set `KUBECONFIG` if you have other contexts.

The first apply takes a few minutes: charts are pulled cold, and Temporal blocks
on a hook waiting for Postgres to report Ready before running its schema job.

Tear down with `helmfile destroy`, or `limactl delete -f k3s` for a genuinely
clean slate — CNPG PVCs and CRDs survive `destroy` on purpose.

## Try it

```bash
curl -X POST http://api.127.0.0.1.sslip.io/greet \
  -H 'Content-Type: application/json' -d '{"name":"brandon"}'
# {"message":"Hello, brandon!","workflow_id":"greet-...","seconds":5.07}

curl -X POST http://nexus.127.0.0.1.sslip.io/greet       # workflow-backed, ~5s
curl -X POST http://nexus.127.0.0.1.sslip.io/greet-sync  # inline, ~0.1s
```

Four Python apps in [charts/scaling-demo/apps/](charts/scaling-demo/apps/), run
from ConfigMaps in stock `python:3.12-slim` pods — no image build, at the cost
of a `pip install` on every pod start.

`poc-api` starts `GreetWorkflow` by name, so it shares no code with the worker,
but it has to know `poc-task-queue`. `nexus-api` reaches equivalent work from a
*different* Temporal namespace knowing only an endpoint name — `nexus-worker`
registers that endpoint itself at startup, since the chart has no Nexus support.

`/greet-sync` is the same contract handled inline: no handler workflow, nothing
to replay, and a ~200ms round trip instead of 5s. It must return inside a 10s
deadline, so it can't do the work `/greet` does.

## Scaling

Every replica count here comes from a KEDA ScaledObject; no Deployment sets
`replicas`. Workers scale on Temporal task-queue backlog, HTTP services on
Traefik request rate — the APIs need no instrumentation of their own.

| Workload | Trigger | Range |
|---|---|---|
| `poc-worker` | task-queue backlog | 0–6 |
| `poc-api` | ingress req/s | 1–5 |
| `nexus-worker` | task-queue backlog | 1–4 |
| `nexus-api` | ingress req/s | 1–4 |

Only `poc-worker` reaches zero. Temporal reports backlog for `workflow` and
`activity` task types only — never `nexus` — so a request to a zero-replica
Nexus handler would create no backlog, never wake it, and time out. Cold start
off zero is ~25s (KEDA plus `pip install`); ~5s warm.

## UIs

`*.127.0.0.1.sslip.io` resolves to `127.0.0.1` over public DNS, so there's no
`/etc/hosts` to maintain. Lima binds these to loopback only.

| | |
|---|---|
| Grafana | http://grafana.127.0.0.1.sslip.io (admin / `prom-operator`) |
| Prometheus | http://prometheus.127.0.0.1.sslip.io |
| Alertmanager | http://alertmanager.127.0.0.1.sslip.io |
| Temporal | http://temporal.127.0.0.1.sslip.io |

## It's a PoC

Grafana uses the chart's default password and Temporal runs with TLS and authz
off. One Postgres instance, `backups.enabled: false`. The apps ship source in
ConfigMaps and install dependencies at runtime — build images for anything real.
