from functools import lru_cache

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=60, ge=1, le=1440)
    bootstrap_admin_email: EmailStr | None = None
    bootstrap_admin_password: SecretStr | None = Field(default=None, min_length=12, max_length=128)
    bootstrap_coordinator_email: EmailStr | None = None
    bootstrap_coordinator_password: SecretStr | None = Field(
        default=None, min_length=12, max_length=128
    )

    @model_validator(mode="after")
    def validate_bootstrap_accounts(self):
        for role in ("admin", "coordinator"):
            email = getattr(self, f"bootstrap_{role}_email")
            password = getattr(self, f"bootstrap_{role}_password")
            if bool(email) != bool(password):
                raise ValueError(f"Configure both bootstrap {role} email and password")
        if self.bootstrap_admin_email and self.bootstrap_coordinator_email:
            if self.bootstrap_admin_email.lower() == self.bootstrap_coordinator_email.lower():
                raise ValueError("Bootstrap accounts must use different email addresses")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
