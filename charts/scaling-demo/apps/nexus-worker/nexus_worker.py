"""Nexus HANDLER side.

Lives in the `poc` Temporal namespace. Serves GreetService over a Nexus
Endpoint that it registers itself on startup, and backs each operation with a
real GreetWorkflow execution.

The caller never learns this namespace or task queue — it only knows the
endpoint name. That decoupling is the whole point of Nexus.
"""
import asyncio
import logging
import os
import uuid
from datetime import timedelta

import nexusrpc
import nexusrpc.handler
from temporalio import activity, workflow
from temporalio.api.nexus.v1 import message_pb2 as nexus_message
from temporalio.api.operatorservice.v1 import request_response_pb2 as operator
from temporalio.client import Client
from temporalio.nexus import WorkflowRunOperationContext, workflow_run_operation
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

ADDRESS = os.getenv("TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233")
NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "poc")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "poc-nexus-queue")
ENDPOINT = os.getenv("NEXUS_ENDPOINT", "greet-endpoint")
GREET_SECONDS = float(os.getenv("GREET_SECONDS", "5"))


# --- the contract, shared by both sides -------------------------------------
# Two operations, same signature. A caller cannot tell from the contract that
# one is backed by a workflow and the other isn't -- that's the abstraction.
@nexusrpc.service
class GreetService:
    greet: nexusrpc.Operation[str, str]
    greet_sync: nexusrpc.Operation[str, str]


# --- the underlying Temporal primitives it abstracts -------------------------
@activity.defn(name="greet")
async def greet_activity(name: str) -> str:
    await asyncio.sleep(GREET_SECONDS)
    return f"Hello, {name}!"


@workflow.defn(name="GreetWorkflow")
class GreetWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            greet_activity, name, start_to_close_timeout=timedelta(seconds=120)
        )


# --- the Nexus service handler ----------------------------------------------
@nexusrpc.handler.service_handler(service=GreetService)
class GreetServiceHandler:
    @workflow_run_operation
    async def greet(self, ctx: WorkflowRunOperationContext, name: str):
        # ASYNC: returns a handle, not a result. The operation completes when
        # the workflow does, so a slow greet doesn't hold an RPC open.
        # Cost: one workflow execution per call.
        return await ctx.start_workflow(
            GreetWorkflow.run,
            name,
            id=f"nexus-greet-{uuid.uuid4().hex[:12]}",
        )

    @nexusrpc.handler.sync_operation
    async def greet_sync(self, ctx: nexusrpc.handler.StartOperationContext, name: str) -> str:
        # SYNC: handled inline, no workflow started, so no history and nothing
        # to replay. Must return inside the 10s handler deadline -- which is why
        # this does no sleeping, unlike the activity behind `greet`.
        #
        # A Temporal Client here could instead Signal/Query/Update a workflow,
        # or Update-With-Start. Those are still reliable and still synchronous,
        # but they do touch a workflow, so they cost one execution again.
        logging.info("sync greet for %s (no workflow)", name)
        return f"Hello, {name}! (sync, no workflow)"


async def ensure_endpoint(client: Client) -> None:
    """Register the endpoint, tolerating other replicas doing the same.

    CreateNexusEndpoint is not idempotent — it fails ALREADY_EXISTS — and
    check-then-act can lose the race, so the conflict is caught as well.
    """
    ops = client.service_client.operator_service
    existing = await ops.list_nexus_endpoints(
        operator.ListNexusEndpointsRequest(name=ENDPOINT)
    )
    if existing.endpoints:
        found = existing.endpoints[0]
        target = found.spec.target.worker
        if target.namespace == NAMESPACE and target.task_queue == TASK_QUEUE:
            logging.info("nexus endpoint %r already registered", ENDPOINT)
            return
        # A stale endpoint pointing at the wrong queue routes requests into a
        # black hole, so retarget rather than silently accepting it.
        logging.warning(
            "nexus endpoint %r points at %s/%s, retargeting to %s/%s",
            ENDPOINT, target.namespace, target.task_queue, NAMESPACE, TASK_QUEUE,
        )
        await ops.update_nexus_endpoint(
            operator.UpdateNexusEndpointRequest(
                id=found.id,
                version=found.version,
                spec=nexus_message.EndpointSpec(
                    name=ENDPOINT,
                    target=nexus_message.EndpointTarget(
                        worker=nexus_message.EndpointTarget.Worker(
                            namespace=NAMESPACE, task_queue=TASK_QUEUE
                        )
                    ),
                ),
            )
        )
        return
    spec = nexus_message.EndpointSpec(
        name=ENDPOINT,
        target=nexus_message.EndpointTarget(
            worker=nexus_message.EndpointTarget.Worker(
                namespace=NAMESPACE, task_queue=TASK_QUEUE
            )
        ),
    )
    try:
        await ops.create_nexus_endpoint(operator.CreateNexusEndpointRequest(spec=spec))
        logging.info("nexus endpoint %r registered -> %s/%s", ENDPOINT, NAMESPACE, TASK_QUEUE)
    except RPCError as err:
        if err.status == RPCStatusCode.ALREADY_EXISTS:
            logging.info("nexus endpoint %r won by another replica", ENDPOINT)
        else:
            raise


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = await Client.connect(ADDRESS, namespace=NAMESPACE)
    await ensure_endpoint(client)
    logging.info("nexus handler up: ns=%s queue=%s endpoint=%s", NAMESPACE, TASK_QUEUE, ENDPOINT)
    await Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GreetWorkflow],
        activities=[greet_activity],
        nexus_service_handlers=[GreetServiceHandler()],
    ).run()


if __name__ == "__main__":
    asyncio.run(main())
