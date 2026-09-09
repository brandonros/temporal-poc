# /// script
# requires-python = ">=3.11"
# dependencies = ["temporalio==1.18.1"]
# ///
import argparse
import asyncio
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from order_workflow import Order, OrderSaga
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker


class Service:
    def __init__(self, name: str, directory: Path):
        self.name = name
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{name}.db"
        self.fault = ""
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def apply(self, key: str, compensate: bool = False) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM operations WHERE key = ?", (key,)
            ).fetchone()
            if compensate:
                db.execute(
                    "INSERT INTO operations VALUES (?, 'COMPENSATED') "
                    "ON CONFLICT(key) DO UPDATE SET state='COMPENSATED'",
                    (key,),
                )
            elif row and row[0] == "COMPENSATED":
                raise ApplicationError(
                    "Forward fenced by compensation",
                    type="AlreadyCompensated",
                    non_retryable=True,
                )
            elif not row:
                db.execute("INSERT INTO operations VALUES (?, 'ACTIVE')", (key,))

    def activities(self) -> list:
        @activity.defn(name=f"{self.name}_forward")
        async def forward(key: str) -> None:
            if self.fault == "reject":
                raise ApplicationError(
                    "Business rule rejected",
                    type="BusinessRejected",
                    non_retryable=True,
                )
            await asyncio.to_thread(self.apply, key)
            if self.fault == "lost_reply":
                raise ApplicationError(
                    "Committed but response lost", type="Unavailable"
                )
            if self.fault == "timeout":
                # Let StartToClose expire after commit.
                await asyncio.sleep(3600)

        @activity.defn(name=f"{self.name}_compensate")
        async def compensate(key: str) -> None:
            await asyncio.to_thread(self.apply, key, True)

        return [forward, compensate]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simulated order Saga")
    parser.add_argument("command", choices=["worker", "start"])
    parser.add_argument("--id", default="order-demo-001")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument(
        "--shipping-fault", choices=["", "reject", "lost_reply", "timeout"], default=""
    )
    args = parser.parse_args()
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    queue = os.getenv("TEMPORAL_TASK_QUEUE", "order-saga")
    if args.command == "worker":
        services = [
            Service(name, args.data_dir)
            for name in ("inventory", "payment", "shipping")
        ]
        services[-1].fault = args.shipping_fault
        async with Worker(
            client,
            task_queue=queue,
            workflows=[OrderSaga],
            activities=[fn for service in services for fn in service.activities()],
        ):
            await asyncio.Event().wait()
    else:
        handle = await client.start_workflow(
            OrderSaga.run,
            Order(args.id),
            id=f"order/{args.id}",
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        try:
            print(json.dumps(await handle.result(), indent=2))
        except WorkflowFailureError:
            print(json.dumps(await handle.query(OrderSaga.status), indent=2))
            raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
