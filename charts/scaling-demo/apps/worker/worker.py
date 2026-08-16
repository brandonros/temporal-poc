"""Temporal worker: runs GreetWorkflow off poc-task-queue.

Deliberately slow and low-concurrency so a burst of requests builds a visible
task-queue backlog for the KEDA temporal trigger to react to.
"""
import asyncio
import logging
import os
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

ADDRESS = os.getenv("TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233")
NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "poc")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "poc-task-queue")
GREET_SECONDS = float(os.getenv("GREET_SECONDS", "5"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_ACTIVITIES", "2"))


@activity.defn(name="greet")
async def greet(name: str) -> str:
    activity.logger.info("greeting %s (%.1fs)", name, GREET_SECONDS)
    await asyncio.sleep(GREET_SECONDS)
    return f"Hello, {name}!"


@workflow.defn(name="GreetWorkflow")
class GreetWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            greet,
            name,
            start_to_close_timeout=timedelta(seconds=120),
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = await Client.connect(ADDRESS, namespace=NAMESPACE)
    logging.info(
        "worker up: ns=%s queue=%s greet=%.1fs concurrency=%d",
        NAMESPACE, TASK_QUEUE, GREET_SECONDS, MAX_CONCURRENT,
    )
    await Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GreetWorkflow],
        activities=[greet],
        max_concurrent_activities=MAX_CONCURRENT,
    ).run()


if __name__ == "__main__":
    asyncio.run(main())
