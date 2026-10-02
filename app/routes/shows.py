from uuid import UUID, uuid4
import json

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.auth import require_admin
from app.models import ShowCreate

router = APIRouter(prefix="/shows", tags=["shows"])


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_show(body: ShowCreate, request: Request) -> JSONResponse:
    show_id = uuid4()
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as connection:
        async with connection.transaction(isolation="read_committed"):
            await connection.execute(
                "INSERT INTO shows(id, name, price_paise) VALUES ($1, $2, $3)",
                show_id,
                body.name,
                body.price_paise,
            )
            await connection.copy_records_to_table(
                "seats",
                records=[(show_id, seat_id) for seat_id in body.seats],
                columns=["show_id", "seat_id"],
            )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "id": str(show_id),
            "name": body.name,
            "price_paise": body.price_paise,
            "seats": [{"seat_id": seat_id, "status": "available"} for seat_id in sorted(body.seats)],
            "counts": {"available": len(body.seats), "held": 0, "confirmed": 0, "total": len(body.seats)},
        },
    )


@router.get("/{show_id}")
async def get_show(show_id: UUID, request: Request) -> dict:
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as connection:
        show = await connection.fetchrow(
            "SELECT id, name, price_paise, created_at FROM shows WHERE id = $1",
            show_id,
        )
        if show is None:
            raise HTTPException(status_code=404, detail="show_not_found")
        rows = await connection.fetch(
            "SELECT seat_id, status FROM seats WHERE show_id = $1 ORDER BY seat_id",
            show_id,
        )

    counts = {"available": 0, "held": 0, "confirmed": 0}
    for row in rows:
        counts[row["status"]] += 1
    total = len(rows)
    if sum(counts.values()) != total:
        request.app.state.logger.error(
                json.dumps(
                    {
                        "event": "seat_reconciliation_invariant_failed",
                        "request_id": getattr(request.state, "request_id", None),
                        "show_id": str(show_id),
                        "counts": counts,
                        "total": total,
                    },
                    separators=(",", ":"),
                )
        )
    return {
        "id": str(show["id"]),
        "name": show["name"],
        "price_paise": show["price_paise"],
        "created_at": show["created_at"].isoformat(),
        "seats": [{"seat_id": row["seat_id"], "status": row["status"]} for row in rows],
        "counts": {**counts, "total": total},
        "invariant_holds": sum(counts.values()) == total,
    }