# Engineering Write-Up

## Atomic decision

PostgreSQL `READ COMMITTED` transactions are the arbitration boundary. A request first inserts its `(user_id, idempotency_key)` row. A uniqueness conflict serializes simultaneous uses of the same key. It next upserts and locks `(show_id, user_id)` in `user_show_locks`; this serializes that user's reservations for the show, which is necessary because locking seat rows alone cannot protect a user-wide limit across different seats. Requested seat rows are locked with `ORDER BY seat_id FOR UPDATE`. The service validates the complete set and active-seat count, inserts reservation/history rows, updates every selected seat, and stores the response before committing.

The primary key `(show_id, seat_id)` makes seat identity unique. The seat update is guarded by `status = 'available'` and its affected-row count is checked. Any unexpected mismatch aborts the whole transaction. Sorted row locks avoid lock-order inversion for overlapping multi-seat requests. The operation is all-or-nothing; no partial reservation is returned.

## Idempotency and charging

The idempotency key is stored in `idempotency_keys`, keyed by `(user_id, idempotency_key)`. The request hash covers the show and sorted seat list. A same-hash retry returns the stored status and JSON response without another reservation; an altered request under the same key returns 409. Success and domain declines are persisted in the same transaction as the key. This API does not integrate a payment processor, so it cannot charge money; `amount_paise` is an integer quote. A future payment integration must use an outbox/payment idempotency contract and must not charge inside a retryable database transaction.

## Cancellation and seat state

The first release uses immediate confirmation and explicit owner-only cancellation, not expiring holds. Cancellation locks the reservation, the per-user/show guard, and its currently assigned seats. It returns those seats to `available` only while their `reservation_id` still matches the reservation being cancelled. Reservation-seat history is retained. `held` is accepted as a database/API status for future extension but is not created by this flow.

## Consistency and availability

The PostgreSQL primary is authoritative. If it is unavailable, readiness fails and writes cannot be accepted; the service does not make an availability-biased decision from stale state. Transactions and uniqueness constraints prevent two accepted reservations for one seat. A partition that separates an app instance from the database therefore reduces availability rather than allowing split-brain sales. Database failover behavior and durability depend on the chosen hosting provider's PostgreSQL guarantees.

## Observability and response

Structured request logs include a validated/generated request ID, method, route template, status, and duration; bearer values and request bodies are not logged. `/metrics` exports HTTP request totals/duration, durable confirmed and declined outcomes, idempotent replay and key-mismatch counts, pool occupancy, and available seats grouped by show. The seat gauge is refreshed from PostgreSQL during each scrape and should match `GET /shows/{id}` at scrape time. Grafana dashboards cover throughput, 4xx/5xx rates, pool activity, available seats, decline reasons, and p95 latency.

Page on sustained 5xx responses, readiness failures, pool saturation, elevated database latency, or reconciliation invariant failures. Investigate a spike in seat-taken conflicts as a demand signal; it is expected during a hot-seat sale. Investigate idempotency mismatches as client misuse or abuse. The service logs invariant failures, but a production alerting rule still needs to be configured in the hosting environment.

## AI usage

AI assistance was used to translate the requested behavior into the initial scaffold, transaction outline, test cases, container configuration, metrics, and documentation. The implementation choices (immediate confirmation, explicit cancellation, all-or-nothing multi-seat requests, and PostgreSQL row locking) follow the requested constraints and are documented here. The code and tests still require a full local run and human review before claiming production readiness; generated changes are not evidence of a successful live 20,000-request test.

## Next steps

Replace mock bearer parsing with a verified identity provider, add a formal versioned migration tool, introduce payment orchestration through an outbox, add provider-specific alert rules and backups, and benchmark on provisioned infrastructure. Record the live URL, deployment revision, generator size, database size, and burst output after an authorized deployment.
