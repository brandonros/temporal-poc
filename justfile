set positional-arguments
export KUBECONFIG := justfile_directory() / ".kubeconfig"

default:
    @just --list

# Create/start the k3s VM and prepare Helmfile.
init:
    #!/usr/bin/env bash
    set -eu
    helmfile init
    limactl start --tty=false --name=k3s lima/k3s.yaml
    umask 077
    limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > "$KUBECONFIG"

# Apply registry settings to an existing VM and restart k3s to load them.
registry-config:
    #!/usr/bin/env bash
    set -eu
    limactl shell k3s sudo install -d -m 0755 /etc/rancher/k3s
    limactl shell k3s sudo tee /etc/rancher/k3s/registries.yaml < lima/registries.yaml > /dev/null
    limactl shell k3s sudo chmod 600 /etc/rancher/k3s/registries.yaml
    limactl shell k3s sudo systemctl restart k3s
    limactl shell k3s sudo k3s kubectl wait node --all --for=condition=Ready --timeout=120s

# Install dependencies, publish images, then install the applications.
apply:
    just infra
    just images
    just apps

infra:
    helmfile --selector layer!=application sync

images:
    python3 scripts/images.py build

apps:
    helmfile --selector layer=application sync --skip-needs

diff:
    helmfile diff

status:
    kubectl -n temporal get pods,scaledobjects

greet:
    curl --fail-with-body --silent --show-error http://greeter.127.0.0.1.sslip.io/greet --json '{"name":"world"}'

# Start an order; use lost_reply, reject or timeout to demonstrate rollback.
order id fault="":
    curl --fail-with-body --silent --show-error http://saga.127.0.0.1.sslip.io/orders --json "{\"order_id\":\"$1\",\"shipping_fault\":\"$2\"}"

order-status id:
    curl --fail-with-body --silent --show-error "http://saga.127.0.0.1.sslip.io/orders/$1"

# Remove Helm releases; database volumes remain.
destroy:
    helmfile destroy

# Send a bounded burst and report worker counts and request latency.
demo-load count="30":
    python3 scripts/demo.py load --requests "$1"

# Wait for zero greeter workers before sending the burst.
demo-cold count="30":
    python3 scripts/demo.py load --requests "$1" --cold

# Complete an order and inspect its persistent records.
demo-order:
    python3 scripts/demo.py order

# Lose the shipping response and verify compensation.
demo-rollback:
    python3 scripts/demo.py rollback

# Roll Saga workers after shipping commits, then verify recovery and cleanup.
demo-restart:
    python3 scripts/demo.py restart
