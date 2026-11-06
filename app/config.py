from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./social_studio.db"
    jwt_secret: str = "development-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    url_fetch_timeout_seconds: float = 10
    url_fetch_max_bytes: int = 2_000_000
    discord_webhook_url: str | None = None
    instagram_access_token: str | None = None
    instagram_user_id: str | None = None
    instagram_owner_user_id: int | None = None
    instagram_username: str | None = None
    instagram_api_version: str = "v25.0"
    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None
    instagram_redirect_uri: str | None = None
    instagram_token_encryption_key: str | None = None
    frontend_base_url: str = "http://localhost:5173"
    public_media_base_url: str | None = None
    worker_poll_seconds: float = 2
    worker_lease_seconds: int = 30
    max_publish_attempts: int = 3
    retry_base_seconds: int = 5
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "google/gemini-2.5-flash"
    judge_audit_model: str = "google/gemini-2.5-pro"
    llm_timeout_seconds: float = 30
    llm_temperature: float = 0.7
    llm_top_p: float = 0.9
    llm_top_k: int | None = 40
    llm_max_output_tokens: int = 1000
    llm_max_input_characters: int = 30_000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    rag_db_password: str | None = None
    rag_db_host: str = "127.0.0.1"
    rag_db_port: int = 5433
    image_storage_dir: str = "data/campaign_images"
    image_timeout_seconds: float = 120

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
