# Seat Reservation Service

A JSON API for assigned event seats. PostgreSQL is the system of record: reservations, idempotency outcomes, seat status, and the per-user booking limit are committed in database transactions. Money is represented as integer paise.

## Run locally

Requirements: Docker Compose and Python 3.12+ for host-side tests or the burst script.

```sh
cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/readyz
```

The app applies the idempotent schema at startup. Compose starts PostgreSQL 16, the API, Prometheus, and Grafana. Local endpoints are `http://localhost:8000`, `http://localhost:9090`, and `http://localhost:3000`. Grafana's default local login is `admin` / `local-grafana-change-me`; change all example secrets before exposing the stack.

## API

Create a show with the configured admin bearer token:

```sh
curl -X POST http://localhost:8000/shows \
  -H 'Authorization: Bearer local-admin-change-me' \
  -H 'Content-Type: application/json' \
  -d '{"name":"friday-night","seats":["A1","A2","A12"],"price_paise":25000}'
```

Reserve as the authenticated user. The bearer value is the mock user ID; identity fields in request JSON are rejected.

```sh
curl -X POST http://localhost:8000/shows/SHOW_UUID/reserve \
  -H 'Authorization: Bearer buyer-123' \
  -H 'Idempotency-Key: order-123' \
  -H 'Content-Type: application/json' \
  -d '{"seats":["A12"]}'
```

The reservation is immediately `confirmed`. Requests are all-or-nothing: if any requested seat is unavailable or missing, the entire request is declined with HTTP 409. The default per-user limit is four active seats per show. Same-key/same-payload requests return the stored response; same-key/different-payload requests return 409. Cancellation is `POST /reservations/{reservation_id}/cancel` and is restricted to the owner. A cancelled reservation releases only seats still attached to that reservation.

`GET /shows/{id}` returns each seat, grouped counts, and an invariant indicator. `GET /healthz` is liveness; `GET /readyz` verifies PostgreSQL and returns 503 when it cannot. `/metrics` exposes Prometheus metrics.

## Tests and burst

Install the test dependencies and start a disposable local database:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
docker compose --profile test up -d test-db
TEST_DATABASE_URL='postgresql://seats:test-only-change-me@localhost:5433/seat_tests' pytest -q
```

The integration tests truncate the database named by `TEST_DATABASE_URL`; the Compose `test-db` profile provides a separate database on port 5433. They skip when the variable is unset.

Run the built-in mixed contention test against a running service:

```sh
python scripts/burst.py http://localhost:8000
```

The default profile sends 500 measured requests with concurrency capped at 200. It creates a fresh show, storms two hot seats, retries stored idempotency keys, checks same-key/different-body rejection, and issues parallel requests for one user. It prints status and decline distributions, then validates the final seat invariant and `seats_available` gauge. To request the full burst against a suitably provisioned target:

```sh
ADMIN_TOKEN='your-admin-token' python scripts/burst.py https://YOUR_PUBLIC_HOST \
  --requests 20000 --concurrency 20000 --timeout 180
```

This creates a new show and writes reservations. Use a disposable target and a load generator with sufficient file descriptors, network capacity, and memory. A 20,000-request run is not evidence of 20,000-request capacity unless the generator and database are provisioned and the result is recorded.

## Configuration

Environment variables: `DATABASE_URL`, `ADMIN_TOKEN`, `PER_USER_LIMIT`, `DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE`, `PORT`, and `WEB_CONCURRENCY`. The default pool sizes (20/100) are per worker process. Size them against PostgreSQL's connection limit and the number of app workers/replicas; do not copy the local 100-connection maximum blindly to a small managed database. The local PostgreSQL container allows 250 connections for two app workers.

The customer token parser is intentionally a mock (`Bearer <user_id>`), suitable only for this challenge. Do not use it as production authentication. Configure a unique admin token and database credentials through the hosting platform's secret manager; never deploy `.env.example` values.

## Deployment

The Docker image honors the platform `PORT` variable, runs the same schema and app as Compose, and exposes `/readyz` as its readiness check. Deploy the container to a host that provides persistent PostgreSQL, set `DATABASE_URL` and a strong `ADMIN_TOKEN`, and use one worker with a pool sized to the managed database unless measured capacity supports more. Verify a cold start, `/readyz`, `/metrics`, and `python scripts/burst.py <BASE_URL>` after deployment.

No public URL is checked in: creating one requires access to a hosting account, a provisioned PostgreSQL service, and configured secrets. The service must not be reported as deployed until those checks pass.
