#!/usr/bin/env python3
"""Build source-tagged application images in Lima and push them to Zot."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "registry.127.0.0.1.sslip.io"
APPS = ("greeter", "saga")


def sources(app):
    directory = ROOT / "apps" / app
    return sorted([
        directory / "Dockerfile",
        directory / "requirements.txt",
        *directory.glob("*.py"),
    ])


def image_tag(app):
    digest = hashlib.sha256()
    for path in sources(app):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return "src-" + digest.hexdigest()[:16]


def guest(*args, **kwargs):
    return subprocess.run(["limactl", "shell", "k3s", *args], check=True, **kwargs)


def build(app):
    image = f"{REGISTRY}/{app}:{image_tag(app)}"
    directory = guest(
        "mktemp", "-d", "/tmp/temporal-poc-build.XXXXXXXX",
        capture_output=True, text=True,
    ).stdout.strip()
    if not directory.startswith("/tmp/temporal-poc-build.") or "/" in directory[len("/tmp/"):]:
        raise RuntimeError("Unexpected build directory")
    try:
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for path in sources(app):
                tar.add(path, arcname=path.name)
        guest("tar", "-xf", "-", "-C", directory, input=archive.getvalue())
        guest("sudo", "nerdctl", "build", "--tag", image, directory)
        guest("sudo", "nerdctl", "--insecure-registry", "push", image)
        print(f"Published {image}", flush=True)
    finally:
        guest("rm", "-rf", "--", directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tag", "build"))
    parser.add_argument("app", choices=APPS, nargs="?")
    args = parser.parse_args()
    if args.command == "tag":
        if args.app is None:
            parser.error("tag requires an application")
        print(image_tag(args.app))
    else:
        for app in (args.app,) if args.app else APPS:
            build(app)
