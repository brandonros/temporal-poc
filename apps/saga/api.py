import asyncio
import logging
import os
import re
import uuid
from datetime import timedelta

from aiohttp import web
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
logger = logging.getLogger(__name__)
QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "saga")


async def create_order(request):
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="Expected a JSON object")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Expected a JSON object")
    order_id = body.get("order_id", uuid.uuid4().hex)
    fault = body.get("shipping_fault", "")
    if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
        raise web.HTTPBadRequest(
            text="order_id must be 1–64 letters, digits, underscores or hyphens"
        )
    if fault not in ("", "reject", "lost_reply", "timeout"):
        raise web.HTTPBadRequest(text="Unknown shipping_fault")
    try:
        await request.app["temporal"].start_workflow(
            "OrderSaga",
            {"transaction_id": order_id, "shipping_fault": fault},
            id=f"order/{order_id}",
            task_queue=QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            rpc_timeout=timedelta(seconds=10),
        )
    except WorkflowAlreadyStartedError:
        raise web.HTTPConflict(text="Order ID already exists")
    except RPCError:
        logger.exception("Could not start order")
        return web.json_response(
            {
                "error": "Temporal unavailable; retry with the same order_id",
                "order_id": order_id,
            },
            status=503,
        )
    return web.json_response(
        {"order_id": order_id, "workflow_id": f"order/{order_id}"},
        status=202,
        headers={"Location": f"/orders/{order_id}"},
    )


async def get_order(request):
    order_id = request.match_info["order_id"]
    if not ORDER_ID.fullmatch(order_id):
        raise web.HTTPBadRequest(text="Invalid order_id")
    handle = request.app["temporal"].get_workflow_handle(f"order/{order_id}")
    try:
        description = await handle.describe(rpc_timeout=timedelta(seconds=5))
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise web.HTTPNotFound(text="Order not found")
        raise web.HTTPServiceUnavailable(text="Temporal unavailable")
    # Queries need a worker, which may still be scaling from zero.
    try:
        state = await handle.query("status", rpc_timeout=timedelta(seconds=5))
    except (RPCError, asyncio.TimeoutError):
        state = None
    return web.json_response(
        {
            "order_id": order_id,
            "workflow_status": description.status.name,
            "state": state,
        }
    )


async def health(request):
    return web.json_response({"status": "ok"})


async def build_app():
    app = web.Application()
    app["temporal"] = await Client.connect(
        os.getenv(
            "TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233"
        ),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    app.add_routes(
        [
            web.post("/orders", create_order),
            web.get("/orders/{order_id}", get_order),
            web.get("/healthz", health),
        ]
    )
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(build_app(), port=int(os.getenv("PORT", "8000")), access_log=None)
