from collections.abc import Sequence

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily


class DatabaseMetricsCollector:
    def __init__(self) -> None:
        self.outcomes: Sequence[tuple[str, str, int]] = ()
        self.available_seats: Sequence[tuple[str, int]] = ()
        self.pool_active = 0
        self.replays = 0
        self.key_mismatches = 0

    def collect(self):
        confirmed = CounterMetricFamily(
            "reservations_confirmed",
            "Durable unique idempotency keys that created a reservation.",
        )
        declines = CounterMetricFamily(
            "reservations_declined",
            "Durable unique idempotency keys declined by the reservation rules.",
            labels=["reason"],
        )
        for outcome, reason, count in self.outcomes:
            if outcome == "confirmed":
                confirmed.add_metric([], count)
            elif outcome == "declined":
                declines.add_metric([reason], count)
        yield confirmed
        yield declines

        replays = CounterMetricFamily(
            "idempotent_replays",
            "Durable idempotent requests that returned their stored response.",
        )
        replays.add_metric([], self.replays)
        yield replays

        mismatches = CounterMetricFamily(
            "idempotency_key_mismatches",
            "Durable requests reusing a key with a different payload.",
        )
        mismatches.add_metric([], self.key_mismatches)
        yield mismatches

        available = GaugeMetricFamily(
            "seats_available",
            "Current available seats by show.",
            labels=["show_id"],
        )
        for show_id, count in self.available_seats:
            available.add_metric([show_id], count)
        yield available

        pool = GaugeMetricFamily(
            "db_connection_pool_active_connections",
            "Currently acquired connections in the asyncpg pool.",
        )
        pool.add_metric([], self.pool_active)
        yield pool


def create_metrics() -> tuple[CollectorRegistry, Counter, Histogram, DatabaseMetricsCollector]:
    registry = CollectorRegistry()
    requests = Counter(
        "http_requests_total",
        "HTTP requests handled by the API.",
        ["method", "endpoint", "status_code"],
        registry=registry,
    )
    duration = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds.",
        ["method", "endpoint"],
        registry=registry,
    )
    database = DatabaseMetricsCollector()
    registry.register(database)
    return registry, requests, duration, database