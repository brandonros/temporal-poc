"""Thin HTTP facade over Temporal.

POST /greet {"name": "..."}  -> starts GreetWorkflow, waits for the result,
returns it. Exposes /metrics so the KEDA prometheus trigger has a real signal
to scale this deployment on.

The workflow is started by name, so this service shares no code with the worker.
"""
import asyncio
import logging
import os
import time
import uuid

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from temporalio.client import Client

ADDRESS = os.getenv("TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233")
NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "poc")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "poc-task-queue")
PORT = int(os.getenv("PORT", "8000"))
TIMEOUT = float(os.getenv("GREET_TIMEOUT_SECONDS", "180"))

REQUESTS = Counter("greet_requests_total", "Requests to /greet", ["status"])
LATENCY = Histogram("greet_request_duration_seconds", "End-to-end /greet latency")
INFLIGHT = Gauge("greet_in_flight", "In-flight /greet requests")


async def handle_greet(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get("name") or request.query.get("name") or "world"

    client: Client = request.app["temporal"]
    workflow_id = f"greet-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()

    INFLIGHT.inc()
    try:
        result = await asyncio.wait_for(
            client.execute_workflow(
                "GreetWorkflow",
                name,
                id=workflow_id,
                task_queue=TASK_QUEUE,
            ),
            timeout=TIMEOUT,
        )
    except asyncio.TimeoutError:
        REQUESTS.labels(status="timeout").inc()
        return web.json_response(
            {"error": "workflow did not complete in time", "workflow_id": workflow_id},
            status=504,
        )
    except Exception as exc:
        logging.exception("greet failed")
        REQUESTS.labels(status="error").inc()
        return web.json_response({"error": str(exc), "workflow_id": workflow_id}, status=500)
    finally:
        INFLIGHT.dec()

    elapsed = time.monotonic() - started
    LATENCY.observe(elapsed)
    REQUESTS.labels(status="ok").inc()
    return web.json_response(
        {"message": result, "workflow_id": workflow_id, "seconds": round(elapsed, 2)}
    )


async def handle_metrics(_: web.Request) -> web.Response:
    return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST.split(";")[0])


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def build_app() -> web.Application:
    app = web.Application()
    app["temporal"] = await Client.connect(ADDRESS, namespace=NAMESPACE)
    app.add_routes(
        [
            web.post("/greet", handle_greet),
            web.get("/healthz", handle_health),
            web.get("/metrics", handle_metrics),
        ]
    )
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("api up: ns=%s queue=%s port=%d", NAMESPACE, TASK_QUEUE, PORT)
    web.run_app(build_app(), port=PORT, access_log=None)
