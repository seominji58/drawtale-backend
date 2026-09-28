from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"

    database_url: str = "postgresql+psycopg://drawtale:drawtale@127.0.0.1:5433/drawtale"

    ai_service_url: str = "http://127.0.0.1:8001"
    ai_use_mock: bool = True
    ai_timeout_seconds: float = 60

    azure_storage_connection_string: str = ""
    blob_container_uploads: str = "dev-uploads"
    blob_container_results: str = "dev-results"

    openai_api_key: str = ""
    tts_api_key: str = ""

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
