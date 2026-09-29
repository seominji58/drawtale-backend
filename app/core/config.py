from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"

    database_url: str = "postgresql+psycopg://drawtale:drawtale@127.0.0.1:5433/drawtale"

    ai_service_url: str = "http://127.0.0.1:8001"
    ai_use_mock: bool = True
    ai_timeout_seconds: float = 60

    public_base_url: str = "http://127.0.0.1:8000"
    storage_backend: str = "local"
    local_storage_dir: str = "./storage"
    max_upload_mb: int = 10

    azure_storage_connection_string: str = ""
    blob_container_uploads: str = "dev-uploads"
    blob_container_results: str = "dev-results"

    openai_api_key: str = ""
    openai_timeout_seconds: float = 30
    tts_api_key: str = ""
    tts_timeout_seconds: float = 60

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
