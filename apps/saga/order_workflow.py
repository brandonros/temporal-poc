import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError


@dataclass
class Order:
    transaction_id: str
    shipping_fault: str = ""
    activity_timeout_seconds: float = 30


@workflow.defn
class OrderSaga:
    def __init__(self) -> None:
        self.state: dict = {"status": "PENDING", "failed_compensations": []}

    @workflow.query
    def status(self) -> dict:
        return self.state

    @workflow.run
    async def run(self, order: Order) -> dict:
        if (
            not order.transaction_id
            or order.activity_timeout_seconds <= 0
            or order.shipping_fault not in ("", "reject", "lost_reply", "timeout")
        ):
            raise ApplicationError(
                "Invalid order", type="InvalidOrder", non_retryable=True
            )
        compensations: list[tuple[str, str]] = []
        self.state["status"] = "RUNNING"
        try:
            for service in ("inventory", "payment", "shipping"):
                # Register by key before a service can commit and lose its reply.
                key = f"{order.transaction_id}:{service}"
                compensations.append((service, key))
                await workflow.execute_activity(
                    f"{service}_forward",
                    args=[key, order.shipping_fault if service == "shipping" else ""],
                    start_to_close_timeout=timedelta(
                        seconds=order.activity_timeout_seconds
                    ),
                    schedule_to_close_timeout=timedelta(
                        seconds=order.activity_timeout_seconds * 4 + 10
                    ),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_attempts=3,
                        non_retryable_error_types=[
                            "BusinessRejected",
                            "AlreadyCompensated",
                        ],
                    ),
                )
        except (ActivityError, asyncio.CancelledError) as original:
            self.state["status"] = "COMPENSATING"
            self.state["original_error"] = str(original)
            cleanup = asyncio.create_task(self._compensate(compensations, order))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Shield keeps cleanup alive; we must still await it before closing.
                await asyncio.shield(cleanup)
            if self.state["failed_compensations"]:
                self.state["status"] = "MANUAL_REVIEW_REQUIRED"
                raise ApplicationError(
                    "Order failed; compensation requires reconciliation",
                    self.state,
                    type="CompensationFailed",
                    non_retryable=True,
                ) from original
            self.state["status"] = "COMPENSATED"
            raise
        self.state["status"] = "COMPLETED"
        return self.state

    async def _compensate(self, stack: list[tuple[str, str]], order: Order) -> None:
        for service, key in reversed(stack):
            try:
                await workflow.execute_activity(
                    f"{service}_compensate",
                    key,
                    start_to_close_timeout=timedelta(
                        seconds=order.activity_timeout_seconds
                    ),
                    schedule_to_close_timeout=timedelta(
                        seconds=order.activity_timeout_seconds * 6 + 30
                    ),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        maximum_attempts=5,
                    ),
                )
            except ActivityError as error:
                self.state["failed_compensations"].append(
                    {"service": service, "key": key, "error": str(error)}
                )
                workflow.logger.error("Compensation exhausted for %s", key)
