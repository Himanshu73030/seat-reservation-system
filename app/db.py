from pathlib import Path

import asyncpg

from app.config import Settings

SCHEMA_SQL = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=30,
        server_settings={"application_name": settings.service_name},
    )
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(761942106)")
            await connection.execute(SCHEMA_SQL)
    return pool