# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
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
        min_length=32, description="Cryptographic secret key for signing tokens"
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

    @computed_field
    @property
    def async_database_url(self) -> str:
        """Construct an async PostgreSQL URL for SQLAlchemy + asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def sync_database_url(self) -> str:
        """Construct a synchronous PostgreSQL URL for Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
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