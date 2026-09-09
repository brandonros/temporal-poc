set positional-arguments
export KUBECONFIG := justfile_directory() / ".kubeconfig"

default:
    @just --list

# Create/start the k3s VM and prepare Helmfile.
init:
    #!/usr/bin/env bash
    set -eu
    helmfile init
    limactl start --name=k3s --memory=8 template://k3s
    umask 077
    limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > "$KUBECONFIG"

apply:
    helmfile apply

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
