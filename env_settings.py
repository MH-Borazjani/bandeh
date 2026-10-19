from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Configuration for environment variable loading and parsing behavior
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore unmapped environment variables instead of raising validation errors
        extra="ignore",
        # Allow case-insensitive matching for environment variables (e.g., DEBUG matches debug)
        case_sensitive=False,
    )

    # Core application configuration
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    # Cryptographic secrets
    # SecretStr masks raw values in logs, tracebacks, and string representations
    secret_key: SecretStr = Field(
        ..., min_length=32, description="Cryptographic secret key for signing tokens"
    )

    # PostgreSQL configuration
    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str

    # Redis configuration
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Dynamic database connection URL construction
    @computed_field
    @property
    def database_url(self) -> PostgresDsn:
        """Construct a standardized PostgreSQL DSN from individual connection parameters."""
        # Extract the plaintext value explicitly from SecretStr for DSN assembly
        return PostgresDsn.build(
            scheme="postgresql",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            path=self.postgres_db,
        )


# Singleton pattern using an LRU cache
@lru_cache
def get_settings() -> Settings:
    """Instantiate and cache the application settings.

    Reading configuration files and environment parsing involves disk I/O and
    validation overhead. Caching the instance ensures this runs only once during
    the application lifecycle.
    """
    return Settings()