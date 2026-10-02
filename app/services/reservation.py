import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg


@dataclass(frozen=True)
class ReservationOutcome:
    status_code: int
    payload: dict[str, Any]
    replayed: bool = False
    key_mismatch: bool = False


def request_hash(show_id: UUID, seats: list[str]) -> str:
    canonical = json.dumps(
        {"show_id": str(show_id), "seats": sorted(seats)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _store_outcome(
    connection: asyncpg.Connection,
    user_id: str,
    idempotency_key: str,
    status_code: int,
    payload: dict[str, Any],
    reason: str,
) -> None:
    await connection.execute(
        """
        UPDATE idempotency_keys
        SET status_code = $3,
            response_payload = $4::jsonb,
            outcome_reason = $5,
            completed_at = now()
        WHERE user_id = $1 AND idempotency_key = $2
        """,
        user_id,
        idempotency_key,
        status_code,
        json.dumps(payload, separators=(",", ":")),
        reason,
    )


async def reserve(
    pool: asyncpg.Pool,
    *,
    show_id: UUID,
    user_id: str,
    seats: list[str],
    idempotency_key: str,
    per_user_limit: int,
) -> ReservationOutcome:
    normalized_seats = sorted(seats)
    body_hash = request_hash(show_id, normalized_seats)

    async with pool.acquire() as connection:
        async with connection.transaction(isolation="read_committed"):
            inserted_key = await connection.fetchval(
                """
                INSERT INTO idempotency_keys(user_id, idempotency_key, request_hash)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """,
                user_id,
                idempotency_key,
                body_hash,
            )
            if inserted_key is None:
                existing = await connection.fetchrow(
                    """
                    SELECT request_hash, status_code, response_payload
                    FROM idempotency_keys
                    WHERE user_id = $1 AND idempotency_key = $2
                    FOR UPDATE
                    """,
                    user_id,
                    idempotency_key,
                )
                if existing["request_hash"].strip() != body_hash:
                    await connection.execute(
                        """
                        UPDATE idempotency_keys SET mismatch_count = mismatch_count + 1
                        WHERE user_id = $1 AND idempotency_key = $2
                        """,
                        user_id,
                        idempotency_key,
                    )
                    return ReservationOutcome(
                        409,
                        {"detail": "idempotency_key_reused", "reason": "idempotency_key_reused"},
                        key_mismatch=True,
                    )
                if existing["status_code"] is None:
                    raise RuntimeError("committed idempotency record has no stored outcome")
                await connection.execute(
                    """
                    UPDATE idempotency_keys SET replay_count = replay_count + 1
                    WHERE user_id = $1 AND idempotency_key = $2
                    """,
                    user_id,
                    idempotency_key,
                )
                payload = existing["response_payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                return ReservationOutcome(
                    existing["status_code"],
                    payload,
                    replayed=True,
                )

            show = await connection.fetchrow(
                "SELECT price_paise FROM shows WHERE id = $1",
                show_id,
            )
            if show is None:
                payload = {"detail": "show_not_found", "reason": "show_not_found"}
                await _store_outcome(connection, user_id, idempotency_key, 404, payload, "show_not_found")
                return ReservationOutcome(404, payload)

            await connection.execute(
                """
                INSERT INTO user_show_locks(show_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT (show_id, user_id) DO NOTHING
                """,
                show_id,
                user_id,
            )
            await connection.fetchrow(
                """
                SELECT show_id FROM user_show_locks
                WHERE show_id = $1 AND user_id = $2
                FOR UPDATE
                """,
                show_id,
                user_id,
            )

            seat_rows = await connection.fetch(
                """
                SELECT seat_id, status FROM seats
                WHERE show_id = $1 AND seat_id = ANY($2::text[])
                ORDER BY seat_id
                FOR UPDATE
                """,
                show_id,
                normalized_seats,
            )
            if len(seat_rows) != len(normalized_seats):
                payload = {"detail": "seat_not_found", "reason": "seat_not_found"}
                await _store_outcome(connection, user_id, idempotency_key, 409, payload, "seat_not_found")
                return ReservationOutcome(409, payload)
            if any(row["status"] != "available" for row in seat_rows):
                payload = {"detail": "seat_taken", "reason": "seat_taken"}
                await _store_outcome(connection, user_id, idempotency_key, 409, payload, "seat_taken")
                return ReservationOutcome(409, payload)

            active_seats = await connection.fetchval(
                """
                SELECT count(*)
                FROM reservation_seats rs
                JOIN reservations r
                  ON r.id = rs.reservation_id AND r.show_id = rs.show_id
                WHERE r.show_id = $1 AND r.user_id = $2 AND r.status = 'confirmed'
                """,
                show_id,
                user_id,
            )
            if active_seats + len(normalized_seats) > per_user_limit:
                payload = {"detail": "per_user_limit", "reason": "per_user_limit"}
                await _store_outcome(connection, user_id, idempotency_key, 409, payload, "per_user_limit")
                return ReservationOutcome(409, payload)

            reservation_id = uuid4()
            amount_paise = show["price_paise"] * len(normalized_seats)
            payload = {
                "reservation_id": str(reservation_id),
                "show_id": str(show_id),
                "user_id": user_id,
                "seats": normalized_seats,
                "amount_paise": amount_paise,
                "status": "confirmed",
            }
            await connection.execute(
                """
                INSERT INTO reservations(id, show_id, user_id, amount_paise, status)
                VALUES ($1, $2, $3, $4, 'confirmed')
                """,
                reservation_id,
                show_id,
                user_id,
                amount_paise,
            )
            await connection.executemany(
                """
                INSERT INTO reservation_seats(reservation_id, show_id, seat_id)
                VALUES ($1, $2, $3)
                """,
                [(reservation_id, show_id, seat_id) for seat_id in normalized_seats],
            )
            update_result = await connection.execute(
                """
                UPDATE seats
                SET status = 'confirmed', reservation_id = $3
                WHERE show_id = $1 AND seat_id = ANY($2::text[]) AND status = 'available'
                """,
                show_id,
                normalized_seats,
                reservation_id,
            )
            if update_result != f"UPDATE {len(normalized_seats)}":
                raise RuntimeError("locked seat set changed during reservation")

            await _store_outcome(connection, user_id, idempotency_key, 201, payload, "confirmed")
            return ReservationOutcome(201, payload)


async def cancel_reservation(
    pool: asyncpg.Pool,
    *,
    reservation_id: UUID,
    user_id: str,
) -> tuple[int, dict[str, Any]]:
    async with pool.acquire() as connection:
        async with connection.transaction(isolation="read_committed"):
            reservation = await connection.fetchrow(
                """
                SELECT id, show_id, user_id, status
                FROM reservations WHERE id = $1 FOR UPDATE
                """,
                reservation_id,
            )
            if reservation is None or reservation["user_id"] != user_id:
                return 404, {"detail": "reservation_not_found"}

            await connection.fetchrow(
                """
                SELECT show_id FROM user_show_locks
                WHERE show_id = $1 AND user_id = $2
                FOR UPDATE
                """,
                reservation["show_id"],
                user_id,
            )
            seat_rows = await connection.fetch(
                """
                SELECT s.seat_id
                FROM seats s
                WHERE s.show_id = $1 AND s.reservation_id = $2
                ORDER BY s.seat_id
                FOR UPDATE OF s
                """,
                reservation["show_id"],
                reservation_id,
            )
            original_seat_rows = await connection.fetch(
                """
                SELECT seat_id FROM reservation_seats
                WHERE show_id = $1 AND reservation_id = $2
                ORDER BY seat_id
                """,
                reservation["show_id"],
                reservation_id,
            )
            seat_ids = [row["seat_id"] for row in original_seat_rows]
            if reservation["status"] == "confirmed":
                await connection.execute(
                    """
                    UPDATE seats
                    SET status = 'available', reservation_id = NULL
                    WHERE show_id = $1 AND reservation_id = $2
                    """,
                    reservation["show_id"],
                    reservation_id,
                )
                await connection.execute(
                    """
                    UPDATE reservations SET status = 'cancelled', cancelled_at = now()
                    WHERE id = $1 AND status = 'confirmed'
                    """,
                    reservation_id,
                )

            return 200, {
                "reservation_id": str(reservation_id),
                "show_id": str(reservation["show_id"]),
                "user_id": user_id,
                "seats": seat_ids,
                "status": "cancelled",
            }