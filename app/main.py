import json
import logging
import re
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import Settings, get_settings
from app.db import create_pool
from app.metrics import create_metrics
from app.routes import health, reservations, shows

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("seat_reservation")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def create_app(settings: Settings | None = None, *, initialize_database: bool = True) -> FastAPI:
    app_settings = settings or get_settings()
    logger = _configure_logger()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.logger = logger
        if initialize_database:
            app.state.pool = await create_pool(app_settings)
        try:
            yield
        finally:
            if initialize_database:
                await app.state.pool.close()

    app = FastAPI(title="Seat Reservation Service", version="0.1.0", lifespan=lifespan)
    registry, requests, duration, database_metrics = create_metrics()
    app.state.settings = app_settings
    app.state.logger = logger
    app.state.registry = registry
    app.state.metrics = type(
        "Metrics",
        (),
        {"requests": requests, "duration": duration, "database": database_metrics},
    )()

    @app.middleware("http")
    async def request_observability(request: Request, call_next) -> Response:
        incoming_id = request.headers.get("X-Request-ID", "")
        request_id = incoming_id if REQUEST_ID_PATTERN.fullmatch(incoming_id) else str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response: Response | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            endpoint = getattr(request.scope.get("route"), "path", "unmatched")
            elapsed = time.perf_counter() - started
            app.state.metrics.requests.labels(request.method, endpoint, str(status_code)).inc()
            app.state.metrics.duration.labels(request.method, endpoint).observe(elapsed)
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "duration_seconds": round(elapsed, 6),
                    },
                    separators=(",", ":"),
                )
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id

    app.include_router(health.router)
    app.include_router(shows.router)
    app.include_router(reservations.router)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        logger.error(
            json.dumps(
                {
                    "event": "unhandled_exception",
                    "request_id": request_id,
                    "exception_type": type(error).__name__,
                },
                separators=(",", ":"),
            )
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "internal_server_error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        pool = getattr(app.state, "pool", None)
        if pool is not None:
            async with pool.acquire() as connection:
                outcome_rows = await connection.fetch(
                    """
                    SELECT outcome_reason,
                           CASE WHEN status_code = 201 THEN 'confirmed' ELSE 'declined' END AS outcome,
                           count(*) AS count
                    FROM idempotency_keys
                    WHERE status_code IS NOT NULL
                    GROUP BY outcome_reason, status_code
                    """
                )
                available_rows = await connection.fetch(
                    """
                    SELECT show_id::text AS show_id, count(*) AS count
                    FROM seats WHERE status = 'available'
                    GROUP BY show_id ORDER BY show_id
                    """
                )
                idempotency_totals = await connection.fetchrow(
                    """
                    SELECT coalesce(sum(replay_count), 0) AS replays,
                           coalesce(sum(mismatch_count), 0) AS mismatches
                    FROM idempotency_keys
                    """
                )
            app.state.metrics.database.outcomes = [
                (row["outcome"], row["outcome_reason"], row["count"])
                for row in outcome_rows
            ]
            app.state.metrics.database.available_seats = [
                (row["show_id"], row["count"]) for row in available_rows
            ]
            app.state.metrics.database.replays = idempotency_totals["replays"]
            app.state.metrics.database.key_mismatches = idempotency_totals["mismatches"]
            app.state.metrics.database.pool_active = pool.get_size() - pool.get_idle_size()
        return Response(
            content=generate_latest(app.state.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


app = create_app()