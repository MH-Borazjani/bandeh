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

    # SMS Provider Configuration (Meli Payamak). The ApiKey is the web
    # service password from the panel developer menu — NOT the panel
    # login password. Never log it.
    meli_sms_username: str | None = Field(
        default=None, description="Meli Payamak panel username"
    )
    meli_sms_api_key: SecretStr | None = Field(
        default=None,
        description="Meli Payamak ApiKey (web service password)",
    )
    meli_sms_from: str | None = Field(
        default=None, description="Meli Payamak sender number"
    )
    meli_sms_owner_phone: str | None = Field(
        default=None,
        description="Shop owner phone for order/low-stock alerts",
    )

    # Meli Payamak pattern codes (bodyId). Each pattern is created by
    # the user in the panel (Special tools -> Service web service) and
    # approved there; the code is per pattern, never hardcoded.
    # Low-stock alerts reuse the owner pattern; payment reminders reuse
    # the order pattern (see services/sms_service.py).
    meli_sms_otp_body_id: int | None = Field(
        default=None, description="Pattern code for the OTP message"
    )
    meli_sms_order_body_id: int | None = Field(
        default=None, description="Pattern code for the order-paid message"
    )
    meli_sms_tracking_body_id: int | None = Field(
        default=None, description="Pattern code for the tracking message"
    )
    meli_sms_owner_body_id: int | None = Field(
        default=None, description="Pattern code for owner alerts"
    )

    # Meilisearch search index. The index is a derived copy of the
    # Directus catalogue (never a source of truth). Both are optional:
    # without them search falls back to the Directus substring search.
    # The master key is also the key the search container itself gets
    # (compose.prod.yaml); never log it.
    meili_url: str | None = Field(
        default=None, description="Meilisearch base URL, e.g. http://search:7700"
    )
    meili_master_key: SecretStr | None = Field(
        default=None,
        description="Meilisearch master key (never log it)",
    )

    # Directus Flow secret. Directus is the shell: it shows the data
    # and triggers actions, but the logic lives in this code. A
    # Directus Flow calls the internal endpoints below with this
    # secret in the X-Directus-Flow-Secret header (never log it).
    # If unset, the internal endpoints return 503.
    directus_flow_secret: SecretStr | None = Field(
        default=None,
        description="Shared secret for Directus Flow webhooks (never log it)",
    )

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