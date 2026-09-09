# temporal-poc

A playground for operating Temporal on k3s: durable workflows, autoscaling, and
compensation. Helmfile installs Temporal, Postgres, KEDA, and monitoring.

- **Greeter:** an API starts a five-second workflow; KEDA scales its worker from zero.
- **Saga:** an API starts a simulated order; its worker compensates partial failures.
  Operation records persist in Postgres. See [Saga details](apps/saga/README.md).

Both have an API and worker, separate task queues, and the `default` Temporal namespace.
Python lives in `apps/{greeter,saga}/`, app-template values in `values/{greeter,saga}/`,
and shared infrastructure in `values/infra/`. Chart versions are pinned in `helmfile.yaml`.

## Run

```bash
brew install lima helmfile just
just init
just apply
just greet
just order demo-001 lost_reply
just order-status demo-001
```

`just` lists the commands. `just init` prepares an 8 GiB VM and writes a repo-local
`.kubeconfig`; recipes use it without changing your global Kubernetes config.
Use a new order ID each time. Cold workers wait for KEDA polling and dependency installation.

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
Grafana uses the chart's default password, and Postgres has one instance without backups.
