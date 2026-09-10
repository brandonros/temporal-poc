# Shared infrastructure

Each folder groups Helm values by responsibility. Release versions and install
dependencies are defined in [`helmfile.yaml`](../../helmfile.yaml).

| Folder | Component | Role in this project |
|---|---|---|
| `networking/` | [Traefik](networking/traefik.yaml) | Routes incoming HTTP traffic to apps, the registry, and UIs |
| `networking/` | [cert-manager](networking/cert-manager.yaml) | Issues certificates for the Barman plugin's client/server connections |
| `storage/` | [SeaweedFS](storage/seaweedfs.yaml) | Provides S3 object storage for logs, traces, and Postgres backups |
| `databases/` | [CloudNativePG](databases/cloudnative-pg.yaml) | Installs the operator that manages Postgres clusters |
| `databases/` | [Barman](databases/barman.yaml) | Adds S3 backup and WAL archiving support to CloudNativePG |
| `registry/` | [Zot](registry/zot.yaml) | Stores locally built application container images |
| `autoscaling/` | [KEDA](autoscaling/keda.yaml) | Scales app workloads based on demand, including workers from zero |
| `observability/` | [kube-prometheus-stack](observability/kube-prometheus-stack.yaml) | Collects metrics with Prometheus, displays dashboards with Grafana, and routes alerts with Alertmanager |
| `observability/` | [Loki](observability/loki.yaml) | Stores and queries pod logs |
| `observability/` | [Tempo](observability/tempo.yaml) | Stores and queries traces |
| `observability/` | [Alloy](observability/alloy.yaml) | Collects pod logs for Loki and receives traces to forward to Tempo |

The actual Postgres cluster and Temporal service are configured in
[`../platform/`](../platform/). Application APIs and workers are configured in
[`../greeter/`](../greeter/) and [`../saga/`](../saga/).

Persistent volumes use k3s local-path storage, configured through
[`lima/k3s.yaml`](../../lima/k3s.yaml).
