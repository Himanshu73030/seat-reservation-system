from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://seats:seats@localhost:5432/seats"
    admin_token: str = "local-admin-change-me"
    per_user_limit: int = Field(default=4, ge=1)
    db_pool_min_size: int = Field(default=20, ge=1)
    db_pool_max_size: int = Field(default=100, ge=1)
    service_name: str = "seat-reservation-service"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.db_pool_min_size > settings.db_pool_max_size:
        raise ValueError("DB_POOL_MIN_SIZE cannot exceed DB_POOL_MAX_SIZE")
    return settings