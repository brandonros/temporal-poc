"""Nexus CALLER side.

Runs in its own Temporal namespace (`caller`) and reaches GreetService purely
through the Nexus Endpoint name. It never references the handler's namespace or
task queue — contrast with api.py, which must know `poc-task-queue`.

One process, two roles:
  - aiohttp server exposing POST /greet
  - an embedded Temporal worker running CallerWorkflow, because Nexus
    operations are invoked from workflows, not from plain clients
"""
import asyncio
import logging
import os
import time
import uuid
from datetime import timedelta

import nexusrpc
from aiohttp import web
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

ADDRESS = os.getenv("TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233")
NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "caller")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "caller-task-queue")
ENDPOINT = os.getenv("NEXUS_ENDPOINT", "greet-endpoint")
PORT = int(os.getenv("PORT", "8000"))
TIMEOUT = float(os.getenv("GREET_TIMEOUT_SECONDS", "180"))


# Same contract as the handler declares. In a real setup this would be a shared
# package published by the handler's team -- the only thing the caller imports.
@nexusrpc.service
class GreetService:
    greet: nexusrpc.Operation[str, str]
    greet_sync: nexusrpc.Operation[str, str]


@workflow.defn(name="CallerWorkflow")
class CallerWorkflow:
    """Calls the workflow-backed operation: 2 workflows total (this + handler)."""

    @workflow.run
    async def run(self, name: str) -> str:
        client = workflow.create_nexus_client(service=GreetService, endpoint=ENDPOINT)
        return await client.execute_operation(
            GreetService.greet,
            name,
            schedule_to_close_timeout=timedelta(seconds=150),
        )


@workflow.defn(name="CallerSyncWorkflow")
class CallerSyncWorkflow:
    """Calls the sync operation: 1 workflow total (this one only).

    Identical caller code -- the only change is which operation is named.
    """

    @workflow.run
    async def run(self, name: str) -> str:
        client = workflow.create_nexus_client(service=GreetService, endpoint=ENDPOINT)
        return await client.execute_operation(
            GreetService.greet_sync,
            name,
            schedule_to_close_timeout=timedelta(seconds=30),
        )


async def _greet(request: web.Request, wf, prefix: str, mode: str) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get("name") or request.query.get("name") or "world"

    client: Client = request.app["temporal"]
    workflow_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            client.execute_workflow(wf, name, id=workflow_id, task_queue=TASK_QUEUE),
            timeout=TIMEOUT,
        )
    except asyncio.TimeoutError:
        return web.json_response(
            {"error": "timed out", "workflow_id": workflow_id}, status=504
        )
    except Exception as exc:
        logging.exception("nexus greet failed")
        return web.json_response(
            {"error": str(exc), "workflow_id": workflow_id}, status=500
        )
    return web.json_response(
        {
            "message": result,
            "via": f"nexus endpoint {ENDPOINT!r}",
            "mode": mode,
            "caller_namespace": NAMESPACE,
            "workflow_id": workflow_id,
            "seconds": round(time.monotonic() - started, 2),
        }
    )


async def handle_greet(request: web.Request) -> web.Response:
    return await _greet(request, CallerWorkflow.run, "caller", "workflow-backed (2 workflows)")


async def handle_greet_sync(request: web.Request) -> web.Response:
    return await _greet(request, CallerSyncWorkflow.run, "caller-sync", "sync op (1 workflow)")


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def run_all() -> None:
    client = await Client.connect(ADDRESS, namespace=NAMESPACE)

    app = web.Application()
    app["temporal"] = client
    app.add_routes([
        web.post("/greet", handle_greet),
        web.post("/greet-sync", handle_greet_sync),
        web.get("/healthz", handle_health),
    ])

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, port=PORT).start()
    logging.info("nexus caller up: ns=%s queue=%s endpoint=%s port=%d",
                 NAMESPACE, TASK_QUEUE, ENDPOINT, PORT)

    await Worker(
        client, task_queue=TASK_QUEUE, workflows=[CallerWorkflow, CallerSyncWorkflow]
    ).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_all())
