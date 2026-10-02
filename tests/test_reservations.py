import asyncio
import os
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.db import SCHEMA_SQL
from app.services.reservation import cancel_reservation, reserve


@pytest.fixture
async def pool():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")
    test_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=32)
    async with test_pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(761942106)")
            await connection.execute(SCHEMA_SQL)
            await connection.execute(
                "TRUNCATE idempotency_keys, reservation_seats, seats, reservations, user_show_locks, shows CASCADE"
            )
    yield test_pool
    await test_pool.close()


async def create_show(pool: asyncpg.Pool, seats: list[str]) -> UUID:
    show_id = uuid4()
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO shows(id, name, price_paise) VALUES ($1, 'test-show', 25000)",
                show_id,
            )
            await connection.copy_records_to_table(
                "seats",
                records=[(show_id, seat_id) for seat_id in seats],
                columns=["show_id", "seat_id"],
            )
    return show_id


@pytest.mark.asyncio
async def test_hot_seat_has_exactly_one_winner(pool: asyncpg.Pool) -> None:
    show_id = await create_show(pool, ["A12"])

    outcomes = await asyncio.gather(
        *(
            reserve(
                pool,
                show_id=show_id,
                user_id=f"buyer-{index}",
                seats=["A12"],
                idempotency_key=f"key-{index}",
                per_user_limit=4,
            )
            for index in range(200)
        )
    )

    assert sum(outcome.status_code == 201 for outcome in outcomes) == 1
    assert sum(outcome.status_code == 409 for outcome in outcomes) == 199
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            "SELECT count(*) FILTER (WHERE status = 'confirmed') AS confirmed, count(*) AS total FROM seats WHERE show_id = $1",
            show_id,
        )
    assert counts["confirmed"] == 1
    assert counts["total"] == 1


@pytest.mark.asyncio
async def test_parallel_requests_cannot_exceed_user_limit(pool: asyncpg.Pool) -> None:
    show_id = await create_show(pool, [f"A{index}" for index in range(1, 11)])

    outcomes = await asyncio.gather(
        *(
            reserve(
                pool,
                show_id=show_id,
                user_id="same-user",
                seats=[f"A{index}"],
                idempotency_key=f"limit-{index}",
                per_user_limit=4,
            )
            for index in range(1, 11)
        )
    )

    assert sum(outcome.status_code == 201 for outcome in outcomes) == 4
    async with pool.acquire() as connection:
        confirmed = await connection.fetchval(
            "SELECT count(*) FROM seats WHERE show_id = $1 AND status = 'confirmed'",
            show_id,
        )
    assert confirmed == 4


@pytest.mark.asyncio
async def test_idempotency_replays_and_rejects_different_body(pool: asyncpg.Pool) -> None:
    show_id = await create_show(pool, ["A1", "A2"])
    first = await reserve(
        pool,
        show_id=show_id,
        user_id="buyer",
        seats=["A1", "A2"],
        idempotency_key="same-key",
        per_user_limit=4,
    )
    replay = await reserve(
        pool,
        show_id=show_id,
        user_id="buyer",
        seats=["A2", "A1"],
        idempotency_key="same-key",
        per_user_limit=4,
    )
    mismatch = await reserve(
        pool,
        show_id=show_id,
        user_id="buyer",
        seats=["A1"],
        idempotency_key="same-key",
        per_user_limit=4,
    )

    assert first.status_code == 201
    assert replay.status_code == 201 and replay.replayed
    assert replay.payload == first.payload
    assert mismatch.status_code == 409 and mismatch.key_mismatch
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            "SELECT count(*) AS reservations, sum(replay_count) AS replays, sum(mismatch_count) AS mismatches FROM idempotency_keys"
        )
    assert counts["reservations"] == 1
    assert counts["replays"] == 1
    assert counts["mismatches"] == 1


@pytest.mark.asyncio
async def test_multi_seat_request_is_all_or_nothing(pool: asyncpg.Pool) -> None:
    show_id = await create_show(pool, ["A1", "A2"])
    taken = await reserve(
        pool,
        show_id=show_id,
        user_id="first",
        seats=["A2"],
        idempotency_key="taken",
        per_user_limit=4,
    )
    declined = await reserve(
        pool,
        show_id=show_id,
        user_id="second",
        seats=["A1", "A2"],
        idempotency_key="all-or-nothing",
        per_user_limit=4,
    )

    assert taken.status_code == 201
    assert declined.status_code == 409
    async with pool.acquire() as connection:
        seat = await connection.fetchrow(
            "SELECT status FROM seats WHERE show_id = $1 AND seat_id = 'A1'",
            show_id,
        )
    assert seat["status"] == "available"


@pytest.mark.asyncio
async def test_cancel_is_owner_only_and_releases_seat(pool: asyncpg.Pool) -> None:
    show_id = await create_show(pool, ["A1"])
    created = await reserve(
        pool,
        show_id=show_id,
        user_id="owner",
        seats=["A1"],
        idempotency_key="initial",
        per_user_limit=4,
    )
    reservation_id = UUID(created.payload["reservation_id"])

    forbidden = await cancel_reservation(pool, reservation_id=reservation_id, user_id="other")
    cancelled = await cancel_reservation(pool, reservation_id=reservation_id, user_id="owner")
    rebooked = await reserve(
        pool,
        show_id=show_id,
        user_id="other",
        seats=["A1"],
        idempotency_key="rebook",
        per_user_limit=4,
    )

    assert forbidden[0] == 404
    assert cancelled[0] == 200 and cancelled[1]["status"] == "cancelled"
    assert rebooked.status_code == 201