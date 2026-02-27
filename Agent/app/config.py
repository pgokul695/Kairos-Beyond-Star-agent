"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Centralised settings — no hardcoded values anywhere else."""

    # Database
    database_url: str = Field(..., env="DATABASE_URL")

    # Google AI
    google_api_key: str = Field(..., env="GOOGLE_API_KEY")
    gemma_model: str = Field("gemini-2.5-flash", env="GEMMA_MODEL")
    gemma_fallback_model: str = Field("gemma-3-12b-it", env="GEMMA_FALLBACK_MODEL")
    embedding_model: str = Field("gemini-embedding-001", env="EMBEDDING_MODEL")
    embedding_dimensions: int = Field(768, env="EMBEDDING_DIMENSIONS")

    # JWT
    jwt_secret: str = Field(..., env="JWT_SECRET")
    jwt_algorithm: str = Field("HS256", env="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(30, env="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(7, env="REFRESH_TOKEN_EXPIRE_DAYS")

    # ProtoPost (password reset email service)
    protopost_base_url: str = Field("https://kairos-t0.gokulp.online", env="PROTOPOST_BASE_URL")
    protopost_auth_token: str = Field("66029484eec5b5729bdb367428ceec71", env="PROTOPOST_AUTH_TOKEN")
    protopost_from_email: str = Field("noreply@kairos.gokulp.online", env="PROTOPOST_FROM_EMAIL")

    # Security
    service_token: str = Field(..., env="SERVICE_TOKEN")
    allowed_origins: str = Field(
        "https://kairos.gokulp.online,http://localhost:3000",
        env="ALLOWED_ORIGINS",
    )

    # App
    app_env: str = Field("development", env="APP_ENV")
    log_level: str = Field("INFO", env="LOG_LEVEL")

    # Local GPU ML (optional — GTX 1650 4 GB VRAM)
    use_local_embeddings: bool = Field(False, env="USE_LOCAL_EMBEDDINGS")
    use_local_reranker: bool = Field(False, env="USE_LOCAL_RERANKER")

    # Derived
    @property
    def allowed_origins_list(self) -> list[str]:
        """Return ALLOWED_ORIGINS as a list."""
        return [o.strip() for o in self.allowed_origins.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()


settings = get_settings()
