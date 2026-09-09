import argparse
import concurrent.futures
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def kubectl(*args):
    return subprocess.check_output(
        ["kubectl", "--request-timeout=15s", "-n", "temporal", *args],
        text=True,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def request(app, path, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://{app}.127.0.0.1.sslip.io{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def wait_for(label, check, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {label} ({seconds}s)")


def replicas(app):
    status = json.loads(kubectl("get", "deployment", f"{app}-worker", "-o", "json"))[
        "status"
    ]
    return status.get("replicas", 0), status.get("readyReplicas", 0)


def load(count, cold):
    if cold:
        print(
            "Waiting for greeter workers to scale to zero; stop other greeter traffic.",
            flush=True,
        )
        wait_for("zero workers", lambda: replicas("greeter") == (0, 0), 360)
    print(
        f"Sending {count} greetings, at most 16 concurrently. Initial workers: {replicas('greeter')}",
        flush=True,
    )

    def greet(index):
        started = time.monotonic()
        result = request("greeter", "/greet", {"name": f"demo-{index}"}, timeout=190)
        if result.get("message") != f"Hello, demo-{index}!":
            raise RuntimeError(f"Unexpected greeting: {result}")
        print(f"Completed {result['workflow_id']}", flush=True)
        return time.monotonic() - started

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        pending = {pool.submit(greet, i) for i in range(count)}
        durations = []
        previous = None
        while pending:
            current = replicas("greeter")
            if current != previous:
                print(f"Workers total/ready: {current}", flush=True)
                previous = current
            done, pending = concurrent.futures.wait(
                pending,
                timeout=2,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            durations.extend(future.result() for future in done)
    print(
        f"PASS: {count} greetings; latency min/median/max: "
        f"{min(durations):.1f}/{statistics.median(durations):.1f}/{max(durations):.1f}s"
    )
    print(
        "KEDA will scale idle workers down after its cooldown; use just status to observe."
    )


def records(order_id):
    # Read through the worker's existing PG credentials; none leave the pod.
    code = """
import json, sys
from worker import connect
with connect() as db:
    rows = db.execute('SELECT key, state FROM operations WHERE key = ANY(%s)',
                      ([sys.argv[1] + ':' + s for s in ('inventory','payment','shipping')],)).fetchall()
print(json.dumps(dict(rows)))
"""
    return json.loads(
        kubectl(
            "exec",
            "deployment/saga-worker",
            "--",
            "python",
            "-c",
            code,
            order_id,
        )
    )


def order(mode):
    kubectl("get", "deployment", "saga-api", "saga-worker", "-o", "name")
    order_id = f"demo-{mode}-{uuid.uuid4().hex[:12]}"
    fault = {"order": "", "rollback": "lost_reply", "restart": "timeout"}[mode]
    body = {"order_id": order_id, "shipping_fault": fault}
    print(f"Order: {order_id}", flush=True)
    print(
        f"Temporal UI: http://temporal.127.0.0.1.sslip.io/namespaces/default/workflows/order%2F{order_id}",
        flush=True,
    )
    try:
        request("saga", "/orders", body)
    except (urllib.error.URLError, TimeoutError):
        # A lost POST response is ambiguous. Check this ID instead of submitting a new order.
        print("POST response unavailable; checking the same order ID.", flush=True)

    if mode == "restart":

        def committed():
            try:
                return records(order_id).get(f"{order_id}:shipping") == "ACTIVE"
            except subprocess.CalledProcessError:
                return False

        wait_for("shipping commit", committed, 180)
        print(
            "Shipping committed. Rolling the Saga workers while the Activity waits.",
            flush=True,
        )
        kubectl("rollout", "restart", "deployment/saga-worker")

    previous = None

    def finished():
        nonlocal previous
        try:
            result = request("saga", f"/orders/{order_id}")
        except (urllib.error.URLError, TimeoutError):
            return None
        if result != previous:
            print(json.dumps(result), flush=True)
            previous = result
        if result.get("state") and result["workflow_status"] != "RUNNING":
            return result
        return None

    result = wait_for("terminal order state", finished, 300)
    expected = "COMPLETED" if mode == "order" else "COMPENSATED"
    if result["state"]["status"] != expected:
        raise RuntimeError(f"Expected {expected}, got {result}")
    actual = records(order_id)
    expected_records = {
        f"{order_id}:{s}": "ACTIVE" if mode == "order" else "COMPENSATED"
        for s in ("inventory", "payment", "shipping")
    }
    if actual != expected_records:
        raise RuntimeError(f"Unexpected database records: {actual}")
    try:
        request("saga", "/orders", body)
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
    else:
        raise RuntimeError("Duplicate order was accepted")
    print(f"PASS: {expected}, database state verified, duplicate order rejected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run demos against the deployed PoC")
    parser.add_argument("demo", choices=["load", "order", "rollback", "restart"])
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--cold", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.requests <= 100:
        parser.error("--requests must be between 1 and 100")
    try:
        if args.demo == "load":
            load(args.requests, args.cold)
        else:
            order(args.demo)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Demo failed: {error}\n")
