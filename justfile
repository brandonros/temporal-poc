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
