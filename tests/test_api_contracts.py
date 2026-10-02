import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_identity_is_not_accepted_from_request_body() -> None:
    app = create_app(Settings(admin_token="test-admin"), initialize_database=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/shows/00000000-0000-0000-0000-000000000001/reserve",
            headers={"Authorization": "Bearer trusted-user", "Idempotency-Key": "key"},
            json={"seats": ["A1"], "user_id": "spoofed-user"},
        )
        unauthenticated = await client.post(
            "/shows/00000000-0000-0000-0000-000000000001/reserve",
            headers={"Idempotency-Key": "key"},
            json={"seats": ["A1"]},
        )

    assert response.status_code == 422
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_readiness_fails_closed_without_database() -> None:
    app = create_app(Settings(admin_token="test-admin"), initialize_database=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert live.status_code == 200
    assert ready.status_code == 503


@pytest.mark.asyncio
async def test_show_price_rejects_json_float() -> None:
    app = create_app(Settings(admin_token="test-admin"), initialize_database=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/shows",
            headers={"Authorization": "Bearer test-admin"},
            json={"name": "show", "seats": ["A1"], "price_paise": 25000.0},
        )

    assert response.status_code == 422