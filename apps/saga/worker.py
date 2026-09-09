import asyncio
import logging
import os

import psycopg
from order_workflow import OrderSaga
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker


def connect():
    return psycopg.connect(connect_timeout=5, options="-c statement_timeout=10000")


def initialize():
    with connect() as db:
        # Serialize schema creation when several worker replicas start together.
        db.execute("SELECT pg_advisory_xact_lock(7233001)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                key TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'COMPENSATED'))
            )
        """)


def apply(key: str, compensate: bool = False):
    with connect() as db:
        if compensate:
            db.execute(
                """
                INSERT INTO operations VALUES (%s, 'COMPENSATED')
                ON CONFLICT (key) DO UPDATE SET state = 'COMPENSATED'
            """,
                (key,),
            )
        else:
            # The upsert locks the row, including when compensation arrives first.
            state = db.execute(
                """
                INSERT INTO operations VALUES (%s, 'ACTIVE')
                ON CONFLICT (key) DO UPDATE SET state = operations.state
                RETURNING state
            """,
                (key,),
            ).fetchone()[0]
            if state == "COMPENSATED":
                raise ApplicationError(
                    "Already compensated", type="AlreadyCompensated", non_retryable=True
                )


def activities(service: str):
    @activity.defn(name=f"{service}_forward")
    async def forward(key: str, fault: str) -> None:
        if fault == "reject":
            raise ApplicationError(
                "Business rule rejected", type="BusinessRejected", non_retryable=True
            )
        await asyncio.to_thread(apply, key)
        if fault == "lost_reply":
            raise ApplicationError("Committed but response lost", type="Unavailable")
        if fault == "timeout":
            await asyncio.sleep(3600)

    @activity.defn(name=f"{service}_compensate")
    async def compensate(key: str) -> None:
        await asyncio.to_thread(apply, key, True)

    return [forward, compensate]


async def main():
    await asyncio.to_thread(initialize)
    client = await Client.connect(
        os.getenv(
            "TEMPORAL_ADDRESS", "temporal-frontend.temporal.svc.cluster.local:7233"
        ),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    await Worker(
        client,
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "saga"),
        workflows=[OrderSaga],
        activities=[
            fn
            for service in ("inventory", "payment", "shipping")
            for fn in activities(service)
        ],
        max_concurrent_activities=4,
    ).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
