# Saga

The API accepts an order; the worker reserves inventory, charges a payment, and
creates a shipment. These are simulated operations stored in the `saga` Postgres
database. Both API and worker are deployed by Helmfile, using the `saga` task queue.

```bash
curl -i http://saga.127.0.0.1.sslip.io/orders \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"demo-001","shipping_fault":"lost_reply"}'

curl http://saga.127.0.0.1.sslip.io/orders/demo-001
```

POST returns `202` with the order and workflow IDs. GET returns Temporal's
execution status and the Saga state. State can be `null` while no worker is
available to answer the query. Use a new order ID for each transaction; duplicate
starts return `409` while Temporal retains the execution.

Omit `shipping_fault` for success. `reject` fails before shipping commits;
`lost_reply` commits but loses the response; `timeout` commits and stalls until
Temporal times out the Activity. Watch retries and cleanup in Temporal's UI.

Compensation stays in the order workflow and runs in reverse order. Keys are
registered before forward calls. Postgres upserts serialize changes to each key,
so duplicate calls are harmless and compensation prevents late forward writes.
The records survive worker restarts and scale-to-zero. This database models
service state; it does not make calls to real providers atomic.

Forward Activities get three attempts; compensations get five. Failed cleanup
reports `MANUAL_REVIEW_REQUIRED` and preserves the original cause. Cancellation
waits for cleanup; termination bypasses it. There is no operator recovery system.

The demo reuses CNPG's generated application credentials for its separate database.
Order IDs must remain unique beyond Temporal's history retention because the
simulated operation records persist.
