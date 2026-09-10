set positional-arguments
export KUBECONFIG := justfile_directory() / ".kubeconfig"

default:
    @just --list

# Create/start the VM and write its kubeconfig.
init:
    #!/usr/bin/env bash
    set -eu
    helmfile init
    limactl start --tty=false --name=k3s lima/k3s.yaml
    umask 077
    limactl shell k3s sudo cat /etc/rancher/k3s/k3s.yaml > "$KUBECONFIG"

# Install dependencies, publish images, then deploy applications.
apply:
    helmfile --selector layer!=application sync
    python3 scripts/images.py build
    helmfile --selector layer=application sync --skip-needs

diff:
    helmfile diff

status:
    kubectl -n temporal get pods,scaledobjects

# Remove Helm releases; volumes and CRDs remain.
destroy:
    helmfile destroy

# Run load, order, rollback, or restart; pass --help for options.
demo +args:
    python3 scripts/demo.py "$@"
