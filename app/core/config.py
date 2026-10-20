from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=60, ge=1, le=1440)
    resource_admin_token: SecretStr | None = Field(default=None, min_length=32)


@lru_cache
def get_settings() -> Settings:
    return Settings()
