from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse

from app.auth import current_user_id
from app.models import ReservationCreate
from app.services.reservation import cancel_reservation, reserve

router = APIRouter(tags=["reservations"])


@router.post("/shows/{show_id}/reserve")
async def reserve_seats(
    show_id: UUID,
    body: ReservationCreate,
    request: Request,
    user_id: str = Depends(current_user_id),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> JSONResponse:
    outcome = await reserve(
        request.app.state.pool,
        show_id=show_id,
        user_id=user_id,
        seats=body.seats,
        idempotency_key=idempotency_key,
        per_user_limit=request.app.state.settings.per_user_limit,
    )
    headers = {"Idempotency-Replayed": "true"} if outcome.replayed else {}
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload, headers=headers)


@router.post("/reservations/{reservation_id}/cancel")
async def cancel(
    reservation_id: UUID,
    request: Request,
    user_id: str = Depends(current_user_id),
) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    result_status, payload = await cancel_reservation(
        pool,
        reservation_id=reservation_id,
        user_id=user_id,
    )
    return JSONResponse(status_code=result_status, content=payload)