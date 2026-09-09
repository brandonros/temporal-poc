# Order Saga

Reserve inventory, charge a payment, create a shipment; compensate in reverse
order on failure. `order_workflow.py` contains the workflow. `run.py` supplies
simulated services backed by separate SQLite files and a command-line runner.

With Python 3.11+ and `uv`, run these in separate terminals from this directory:

```bash
temporal server start-dev
uv run run.py worker --shipping-fault lost_reply
uv run run.py start --id demo-001
```

The client prints `COMPENSATED` and exits with the original failure. Inspect
history at http://localhost:8233. Omit `--shipping-fault` and use a new ID for
success. `uv` reads the script's dependency declaration; no project setup needed.

For k3s, port-forward `svc/temporal-frontend` in namespace `temporal` to port 7233
The scripts use the same `default` Temporal namespace as the scaling demo.

Compensation stays inline: the caller waits for cleanup. Keys are registered
before forward calls, so a lost reply cannot hide a committed resource. SQLite
records make retries idempotent and reject late writes after compensation.
These local transactions don't make real payment-provider calls atomic.

Forward Activities get three attempts; compensations get five. Cleanup continues
if one compensation fails, then reports `MANUAL_REVIEW_REQUIRED` with the original
cause. Cancellation waits for cleanup; termination bypasses it. There is no
operator recovery system in this demo.
