"""
Configuration module for the Stock Screener and Fundamental Analysis Pipeline.

Loads settings from environment variables and `.env` file using Pydantic Settings.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings and configuration parameters."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL Database Configuration
    DB_HOST: str = Field(
        default="localhost",
        description="Hostname or IP address of the PostgreSQL database server.",
    )
    DB_PORT: int = Field(
        default=5432,
        description="Port of the PostgreSQL database server.",
    )
    DB_USER: str = Field(
        default="postgres",
        description="Username for PostgreSQL authentication.",
    )
    DB_PASSWORD: str = Field(
        default="postgres",
        description="Password for PostgreSQL authentication.",
    )
    DB_NAME: str = Field(
        default="stock_screener",
        description="Database name in PostgreSQL.",
    )
    DB_MIN_POOL_SIZE: int = Field(
        default=2,
        description="Minimum number of active connections in the psycopg connection pool.",
    )
    DB_MAX_POOL_SIZE: int = Field(
        default=10,
        description="Maximum number of active connections in the psycopg connection pool.",
    )

    # SEC EDGAR & HTTP Networking
    SEC_USER_AGENT: str = Field(
        default="StockScreener admin@example.com",
        description=(
            "Custom User-Agent header string required by SEC EDGAR fair-access policy. "
            "Format: Sample Company Name AdminContact@<domain>.com"
        ),
    )
    REQUEST_TIMEOUT: int = Field(
        default=15,
        description="HTTP request timeout in seconds.",
    )

    # Worker Concurrency & Rate Limiting
    WORKER_CONCURRENCY: int = Field(
        default=8,
        description="Number of concurrent worker threads for data fetching.",
    )
    RATE_LIMIT_PER_SEC: float = Field(
        default=8.0,
        description="Maximum requests per second allowed across all threads.",
    )

    # Fundamental Data Cache Expiry
    DATA_MAX_AGE_DAYS: int = Field(
        default=30,
        description="Threshold in days before cached company fundamentals are considered stale.",
    )

    @property
    def conn_string(self) -> str:
        """Construct the PostgreSQL connection string."""
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            "?connect_timeout=5"
        )


# Global settings singleton
settings = Settings()
